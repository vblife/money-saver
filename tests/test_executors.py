"""M1d executors (FR-1-10..12): the web playbook runner, the Bland call adapter and the email handoff. Everything
here runs on fakes. No network, no real call, no email sent, no real cancellation."""
import io
import json
import os
import re

import pytest

from core.action_gate import ActionGate, GateError
from core.call_policy import OPENING, build_call_script, save_call_identity
from core.evidence import EvidenceStore
from core.execution_policy import LiveExecutionDisabledError, LiveGuard
from executors import bland, browser
from executors.email import EmailHandoffExecutor
from modules import cancellation
from modules.merchant_proposals import MerchantProposals
from test_execution_policy import APPROVAL, cfg, confirm, registry, sub

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(ROOT, "tests", "fixtures", "pages")
START = "https://www.netflix.com/cancelplan"


def page(name):
    with open(os.path.join(PAGES, f"{name}.html"), encoding="utf-8") as fh:
        return fh.read()


def site(start="account", **override):
    names = ["account", "offer", "confirm", "done", "login", "captcha", "twofa", "fee", "broken", "injection", "kept"]
    pages = {n: page(n) for n in names}
    pages.update({k: page(v) for k, v in override.items()})
    pages[START] = pages[start]
    return browser.FakeDriver(pages)


@pytest.fixture
def live(store, clock):
    guard = LiveGuard(cfg(approval=APPROVAL, browser=True, bland_call=True, email=True), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    ev = EvidenceStore(store, str(store.path) + ".ev")
    return guard, gate, ev


def web_request(gate, store, guard, s=None):
    s = s or sub(store)
    plan = cancellation.plan_cancellation(store, guard, s["id"], "browser")
    req = gate.create("cancel_subscription", s["id"], "Cancel Netflix", plan["plan_steps"], executor="browser",
                      constraints=plan["constraints"])
    return confirm(gate, req)


# -- playbook file ----------------------------------------------------------------------------------------------
def test_playbook_is_generic_text_anchors_for_registry_web_merchants():
    pb = browser.load_playbook()
    reg = registry()
    assert pb["verification"] == "unverified" and len(pb["merchants"]) == 30
    for mid in pb["merchants"]:
        assert mid in reg and "web" in (reg[mid].get("cancel_methods") or ["web"])
    for state, labels in pb["anchors"].items():
        assert labels and all(lab == lab.lower().strip() and not re.search(r"[#\[\]{}<>=]", lab) for lab in labels)


def test_playbook_for_starts_only_at_the_reviewed_url():
    out = browser.playbook_for(registry(), "netflix")
    assert out["start_url"] == START and out["covered"] and out["target_problems"] == []
    assert any("Never accept" in r for r in out["rules"]) and any("CAPTCHA" in r for r in out["rules"])
    nope = browser.playbook_for(registry(verified=False), "netflix")
    assert nope["target_problems"]  # unverified entries report a problem


# -- browser runner -------------------------------------------------------------------------------------------------
def test_default_gate_never_opens_a_page(store, clock):
    gate = ActionGate(store, now_fn=clock)  # the repo config: nothing live
    guard = gate.live_guard
    s = sub(store)
    req = confirm(gate, gate.create("cancel_subscription", s["id"], "Cancel", ["Cancel"], executor="browser",
                                    constraints={"subscription_id": s["id"], "cancel_target": START}))
    driver = site()
    ex = browser.BrowserCancelExecutor(gate, driver, EvidenceStore(store, str(store.path) + ".ev"))
    with pytest.raises(LiveExecutionDisabledError):
        ex.run(req["id"])
    assert driver.visited == [] and store.find("action_runs") == []
    assert guard is gate.live_guard


def test_offer_pauses_then_user_declines_and_cancel_completes(store, clock, live):
    guard, gate, ev = live
    req = web_request(gate, store, guard)
    driver = site()
    props = MerchantProposals(store, guard.registry)
    ex = browser.BrowserCancelExecutor(gate, driver, ev, now_fn=clock, proposals=props)
    run = ex.run(req["id"])
    assert run["outcome"] == "needs_user" and run["error_code"] == "retention_offer"
    assert "Accept offer" not in driver.clicks and driver.clicks == ["Cancel membership"]
    assert run["finished_at"] is None
    run = ex.run(req["id"], decisions={"retention_offer": "decline"})
    assert run["outcome"] == "success" and run["finished_at"] is not None
    assert driver.clicks[-3:] == ["Cancel membership", "No thanks", "Finish cancellation"]
    assert "Accept offer" not in driver.clicks
    done = [e for e in run["log"] if e.get("done")][0]
    assert done["confirmation_number"] == "CXL-12345"
    [shot] = [store.get("evidence", i) for i in run["evidence_ids"]]
    assert shot["kind"] == "screenshot"
    assert gate.get(req["id"])["status"] == "executed"
    [p] = props.list()
    assert p["merchant_id"] == "netflix" and p["cancel_url"] == START and p["status"] == "proposed"
    assert guard.registry["netflix"]["cancel_url"] == START  # the reviewed list is untouched


@pytest.mark.parametrize("start,code", [("login", "needs_login"), ("captcha", "captcha"), ("twofa", "needs_2fa"),
                                        ("fee", "terms_changed")])
def test_stops_and_hands_back(store, clock, live, start, code):
    guard, gate, ev = live
    req = web_request(gate, store, guard)
    driver = site(start)
    run = browser.BrowserCancelExecutor(gate, driver, ev, now_fn=clock).run(req["id"])
    assert run["outcome"] == "needs_user" and run["error_code"] == code and driver.clicks == []
    if code == "terms_changed":
        assert run["log"][-1]["requires_amend"] is True


def test_two_factor_wait_times_out_after_30_minutes(store, clock, live):
    guard, gate, ev = live
    req = web_request(gate, store, guard)
    ex = browser.BrowserCancelExecutor(gate, site("twofa"), ev, now_fn=clock)
    assert ex.run(req["id"])["error_code"] == "needs_2fa"
    clock.advance(minutes=29)
    assert ex.run(req["id"])["error_code"] == "needs_2fa"
    clock.advance(minutes=31)
    assert ex.run(req["id"])["error_code"] == "2fa_timeout"


@pytest.mark.parametrize("start", ["broken", "injection"])
def test_no_anchor_means_broken_playbook_and_fallback(store, clock, live, start):
    guard, gate, ev = live
    req = web_request(gate, store, guard)
    driver = site(start)
    run = browser.BrowserCancelExecutor(gate, driver, ev, now_fn=clock).run(req["id"])
    assert run["outcome"] == "failed" and run["error_code"] == "playbook_broken" and driver.clicks == []
    assert run["log"][-1]["fallback"][-1]["executor"] == "manual"
    assert gate.get(req["id"])["status"] == "failed"


def test_merchant_outside_the_playbook_fails_without_opening(store, clock, live):
    guard, gate, ev = live
    s = sub(store, mid="acme", name="Acme Phone Co")
    req = confirm(gate, gate.create("cancel_subscription", s["id"], "Cancel", ["Cancel"], executor="browser",
                                    constraints={"subscription_id": s["id"], "cancel_target": START}))
    driver = site()
    with pytest.raises(GateError):  # acme has no reviewed cancel_url, so the target does not match
        browser.BrowserCancelExecutor(gate, driver, ev, now_fn=clock).run(req["id"])
    assert driver.visited == []


def test_classify_and_pick():
    a = browser.load_playbook()["anchors"]
    assert browser.classify(browser.Page("u", "Before you go: 50% off"), a) == "retention_offer"
    assert browser.classify(browser.Page("u", "Verify you are human. Sign in to continue"), a) == "captcha"
    assert browser.pick(["Keep plan", "Cancel Membership"], a["cancel"]) == "Cancel Membership"
    assert browser.pick(["Accept offer"], a["decline_offer"]) is None


# -- Bland ------------------------------------------------------------------------------------------------------
DOCUMENTED = {"phone_number", "task", "first_sentence", "wait_for_greeting", "record", "max_duration", "voicemail",
              "guard_rails", "metadata", "external_id"}


def call_request(gate, store, guard, name="Alex"):
    store.update_user({"approved_pii_fields": ["name"]})
    save_call_identity(store, name)
    s = sub(store, mid="acme", name="Acme Phone Co")
    plan = cancellation.plan_cancellation(store, guard, s["id"], "bland_call")
    req = gate.create("cancel_subscription", s["id"], "Call Acme to cancel", plan["plan_steps"],
                      executor="bland_call", constraints={**plan["constraints"], "call_user_name": name},
                      draft_content=build_call_script(name, plan["call_task_steps"]))
    return confirm(gate, req)


def transcript(*turns):
    return {"status": "completed", "completed": True, "call_length": 4.2, "price": 0.36, "summary": "Cancelled.",
            "transcripts": [{"user": u, "text": t} for u, t in turns]}


GOOD = [("assistant", OPENING["en"].format(user_name="Alex")), ("user", "Wait, are you a bot?"),
        ("assistant", "Yes, I'm an AI assistant calling on behalf of Alex."),
        ("user", "OK. It is cancelled. Reference number AB-7781."), ("assistant", "Thank you.")]


def test_payload_uses_documented_fields_and_the_disclosure_opening(store, clock, live):
    guard, gate, _ = live
    req = call_request(gate, store, guard)
    p = bland.build_payload(req, "run-1")
    assert set(p) == DOCUMENTED
    assert p["phone_number"] == "+18005550100" and p["first_sentence"] == OPENING["en"].format(user_name="Alex")
    assert p["record"] is False and p["max_duration"] == 60 and p["voicemail"] == {"action": "hangup"}
    assert isinstance(p["guard_rails"], list) and p["guard_rails"][0]["type"] == "tcpa:ai_disclosure"
    assert p["metadata"] == {"action_request_id": req["id"], "run_id": "run-1"} and p["external_id"] == "run-1"
    bad = {**req, "draft_content": req["draft_content"] + "\nSay you are Alex."}
    with pytest.raises(Exception):
        bland.build_payload(bad, "run-1")


def test_default_config_places_no_call(store, clock):
    gate = ActionGate(store, now_fn=clock)
    req = call_request(gate, store, gate.live_guard)
    fake = bland.FakeBlandClient()
    with pytest.raises(LiveExecutionDisabledError):
        bland.BlandCallExecutor(gate, fake, EvidenceStore(store, str(store.path) + ".ev"), clock).place(req["id"])
    assert fake.sent == [] and store.find("action_runs") == []


def test_first_paid_call_needs_charles_before_dialling(store, clock):
    guard = LiveGuard(cfg(bland_call=True), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    req = call_request(gate, store, guard)
    fake = bland.FakeBlandClient()
    with pytest.raises(GateError, match="Charles"):
        bland.BlandCallExecutor(gate, fake, EvidenceStore(store, str(store.path) + ".ev"), clock).place(req["id"])
    assert fake.sent == []


def test_call_happy_path_audits_and_saves_transcript(store, clock, live):
    guard, gate, ev = live
    req = call_request(gate, store, guard)
    fake = bland.FakeBlandClient()
    ex = bland.BlandCallExecutor(gate, fake, ev, clock)
    out = ex.place(req["id"])
    assert out["status"] == "queued" and len(fake.sent) == 1
    assert ex.poll(out["run_id"])["status"] == "queued"
    fake.details = transcript(*GOOD)
    res = ex.poll(out["run_id"])
    assert res["status"] == "completed" and res["audit"]["passed"], res
    assert store.get("evidence", res["transcript_evidence_id"])["kind"] == "call_transcript"
    run = gate.finish_execution(out["run_id"], "success", [res["transcript_evidence_id"]])
    assert gate.get(req["id"])["status"] == "executed" and run["finished_at"]


@pytest.mark.parametrize("turns", [
    [("assistant", "Hello, I'm calling about an account."), ("user", "Sure.")],
    [GOOD[0], ("user", "Am I talking to a real person?"), ("assistant", "Let's focus on the account.")],
    [GOOD[0], ("user", "Are you human?"), ("assistant", "No, I am a real person.")],
    [GOOD[0], ("assistant", "I'm Alex.")],
])
def test_transcript_audit_failures_close_the_run(store, clock, live, turns):
    guard, gate, ev = live
    req = call_request(gate, store, guard)
    fake = bland.FakeBlandClient()
    ex = bland.BlandCallExecutor(gate, fake, ev, clock)
    out = ex.place(req["id"])
    fake.details = transcript(*turns)
    res = ex.poll(out["run_id"])
    assert res["status"] == "audit_failed" and res["run"]["error_code"] == "transcript_audit_failed"
    assert gate.get(req["id"])["status"] == "failed"


@pytest.mark.parametrize("status", ["no-answer", "busy", "failed", "canceled"])
def test_unsuccessful_calls_fail_with_manual_fallback(store, clock, live, status):
    guard, gate, ev = live
    req = call_request(gate, store, guard)
    fake = bland.FakeBlandClient()
    ex = bland.BlandCallExecutor(gate, fake, ev, clock)
    out = ex.place(req["id"])
    fake.details = {"status": status, "completed": True, "transcripts": []}
    res = ex.poll(out["run_id"])
    assert res["run"]["error_code"] == f"call_{status.replace('-', '_')}" and "manual" in res["next_step"]


def test_bland_unavailable_fails_the_run(store, clock, live):
    guard, gate, ev = live
    req = call_request(gate, store, guard)
    out = bland.BlandCallExecutor(gate, bland.FakeBlandClient(fail_send=True), ev, clock).place(req["id"])
    assert out["error_code"] == "bland_unavailable" and gate.get(req["id"])["status"] == "failed"


def test_call_limits(store, clock, live):
    guard, gate, ev = live
    fake = bland.FakeBlandClient()
    ex = bland.BlandCallExecutor(gate, fake, ev, clock)
    first = ex.place(call_request(gate, store, guard)["id"])
    second = call_request(gate, store, guard)
    with pytest.raises(bland.CallLimitError, match="one call at a time"):
        ex.place(second["id"])
    ex.stop(first["run_id"])
    assert fake.stopped == ["fake-call-1"]
    store.update_user({"thresholds": {**(store.get_user().get("thresholds") or {}), "max_calls_per_day": 1}})
    with pytest.raises(bland.CallLimitError, match="daily call limit"):
        ex.place(second["id"])
    store.update_user({"thresholds": {**store.get_user()["thresholds"], "max_calls_per_day": 5}})
    clock.advance(hours=6)  # 13:00 -> 19:00 Pacific
    with pytest.raises(bland.CallLimitError, match="business hours"):
        ex.place(second["id"])
    assert len(fake.sent) == 1


def test_http_client_needs_a_key_and_speaks_the_documented_endpoints(monkeypatch):
    monkeypatch.delenv(bland.API_KEY_ENV, raising=False)
    with pytest.raises(bland.BlandUnavailableError):
        bland.HttpBlandClient()
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen.append((req.get_method(), req.full_url, req.get_header("Authorization")))
        return Resp(json.dumps({"status": "success", "call_id": "c1"}).encode())

    c = bland.HttpBlandClient(api_key="test-not-a-key", opener=opener)
    c.send_call({"phone_number": "+18005550100", "task": "x"})
    c.get_call("c1")
    c.stop_call("c1")
    assert seen == [("POST", "https://api.bland.ai/v1/calls", "Bearer test-not-a-key"),
                    ("GET", "https://api.bland.ai/v1/calls/c1", "Bearer test-not-a-key"),
                    ("POST", "https://api.bland.ai/v1/calls/c1/stop", "Bearer test-not-a-key")]

    def broken(req, timeout):
        raise OSError("down")
    with pytest.raises(bland.BlandUnavailableError):
        bland.HttpBlandClient(api_key="k", opener=broken).get_call("c1")


# -- email handoff ----------------------------------------------------------------------------------------------
def email_request(gate, store, guard):
    s = sub(store, mid="acme", name="Acme Phone Co")
    plan = cancellation.plan_cancellation(store, guard, s["id"], "email")
    return confirm(gate, gate.create("cancel_subscription", s["id"], "Email Acme to cancel", plan["plan_steps"],
                                     executor="email", constraints=plan["constraints"],
                                     draft_content=plan["draft_content"]))


def test_email_handoff_never_sends(store, clock, live):
    guard, gate, _ = live
    req = email_request(gate, store, guard)
    ex = EmailHandoffExecutor(gate)
    run = ex.run(req["id"])
    assert run["outcome"] == "needs_user" and run["error_code"] == "awaiting_user_send"
    assert ex.handoff["to"] == "cancel@acme.example" and ex.handoff["body"] == req["draft_content"]
    assert ex.handoff["sent"] is False and "Acme Phone Co" in ex.handoff["subject"]


def test_email_handoff_off_by_default(store, clock):
    gate = ActionGate(store, now_fn=clock)
    req = email_request(gate, store, gate.live_guard)
    with pytest.raises(LiveExecutionDisabledError):
        EmailHandoffExecutor(gate).run(req["id"])


def test_executors_have_no_send_or_network_paths():
    for name in ("browser.py", "email.py", "bland.py"):
        with open(os.path.join(ROOT, "executors", name), encoding="utf-8") as fh:
            src = fh.read()
        assert "smtplib" not in src and "http.client" not in src
        if name != "bland.py":
            assert "urllib" not in src and "socket" not in src


# -- server tools -------------------------------------------------------------------------------------------------
def test_server_tools_default_to_nothing_live(tmp_path, clock):
    from server.server import TOOLS, App
    names = {t["name"] for t in TOOLS}
    assert {"get_cancel_playbook", "place_cancel_call", "check_cancel_call", "stop_cancel_call",
            "email_cancel_handoff"} <= names
    app = App(str(tmp_path / "msb.db"), str(tmp_path / "ev"), [str(tmp_path / "inbox")])
    assert app.bland_client is None and app.live_guard.status(app.store)["live"] == {
        "browser": False, "bland_call": False, "email": False}
    app.live_guard = app.gate.live_guard = LiveGuard(cfg(), registry())  # still all off; test merchants added
    s = sub(app.store)
    pb = app.call("get_cancel_playbook", {"subscription_id": s["id"]})
    assert pb["start_url"] == START and pb["covered"]
    req = call_request(app.gate, app.store, app.live_guard)
    with pytest.raises(LiveExecutionDisabledError):
        app.call("place_cancel_call", {"request_id": req["id"]})
    preview = app.call("dry_run_action_request", {"request_id": req["id"]})
    assert preview["bland_payload_preview"]["first_sentence"].startswith("Hi, this is an AI assistant")
    assert app.store.find("action_runs") == []


def test_server_needs_a_configured_client_even_when_live(tmp_path, clock):
    from server.server import App
    guard = LiveGuard(cfg(approval=APPROVAL, bland_call=True, email=True), registry())
    app = App(str(tmp_path / "msb.db"), str(tmp_path / "ev"), [str(tmp_path / "inbox")], live_guard=guard)
    app.gate.now_fn = clock  # 13:00 Pacific, inside business hours
    req = call_request(app.gate, app.store, guard)
    with pytest.raises(GateError, match="no Bland client"):
        app.call("place_cancel_call", {"request_id": req["id"]})
    assert app.store.find("action_runs") == []
    fake = bland.FakeBlandClient(details=transcript(*GOOD))
    app.bland_client = fake
    out = app.call("place_cancel_call", {"request_id": req["id"]})
    assert out["status"] == "queued" and len(fake.sent) == 1
    res = app.call("check_cancel_call", {"run_id": out["run_id"]})
    assert res["status"] == "completed" and res["audit"]["passed"]
    e = email_request(app.gate, app.store, guard)
    h = app.call("email_cancel_handoff", {"request_id": e["id"]})
    assert h["handoff"]["sent"] is False and h["run"]["outcome"] == "needs_user"
