"""M1d live-execution gates (D32-D36): off by default, first live cancel per merchant, Charles's first paid call,
reviewed registry targets only, and Q11 proposals that can never drive a live cancellation."""
import copy
import json
import os

import pytest

from core import notifier
from core.action_gate import ActionGate, GateError
from core.call_policy import build_call_script, save_call_identity
from core.execution_policy import (CONFIG_PATH, ExecutionConfig, FirstLiveApprovalRequiredError,
                                   LiveExecutionDisabledError, LiveGuard, PaidCallApprovalRequiredError,
                                   UnreviewedTargetError, load_config, load_registry, parse_config)
from modules import cancellation
from modules.merchant_proposals import MerchantProposals, ProposalError
from modules.subscriptions import add_manual_subscription

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVAL = {"approved_by": "Charles", "approved_on": "2026-10-22", "reference": "chat 2026-10-22 21:25"}


def registry(verified=True):
    reg = copy.deepcopy(load_registry())
    if verified:
        reg["netflix"]["verification"] = "verified"
    reg["acme"] = {**reg["netflix"], "id": "acme", "name": "Acme Phone Co", "cancel_url": None,
                   "support_phone": "+18005550100", "cancel_email": "cancel@acme.example",
                   "verification": "verified" if verified else "unverified"}
    return reg


def cfg(approval=None, **live):
    return ExecutionConfig(live={"browser": False, "bland_call": False, "email": False, **live},
                           bland_first_paid_call_approval=approval)


def sub(store, mid="netflix", name="Netflix", channel="direct"):
    return add_manual_subscription(store, name, 1799, "monthly", merchant_id=mid, account_email="alex@example.com",
                                   billing_channel=channel)


def confirm(gate, req):
    return gate.confirm(req["id"], f"confirm {req['confirmation_code']}")


def cancel_req(gate, store, guard, s, executor="browser", **extra):
    plan = cancellation.plan_cancellation(store, guard, s["id"], executor)
    kw = {"constraints": plan["constraints"], "draft_content": plan["draft_content"]}
    kw.update(extra)
    return gate.create("cancel_subscription", s["id"], f"Cancel {s['display_name']}", plan["plan_steps"],
                       executor=executor, **kw)


# -- config ----------------------------------------------------------------------------------------------------------
def test_repo_config_is_all_off_and_parses_cleanly():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["live"] == {"browser": False, "bland_call": False, "email": False}
    assert doc["bland_first_paid_call_approval"] is None
    c = load_config()
    assert c.error is None and not any(c.is_live(e) for e in ("browser", "bland_call", "email", "manual"))
    assert not c.paid_call_approved()
    assert ExecutionConfig() == ExecutionConfig() and not ExecutionConfig().is_live("browser")


@pytest.mark.parametrize("doc", [
    [], {"live": {"browser": "true"}}, {"live": {"browser": 1}}, {"live": {"phone": True}}, {"live": []},
    {"surprise": True}, {"version": 2, "live": {"browser": True}},
    {"live": {"bland_call": True}, "bland_first_paid_call_approval": {**APPROVAL, "approved_by": "Developer"}},
    {"bland_first_paid_call_approval": {"approved_by": "Charles"}},
    {"bland_first_paid_call_approval": {**APPROVAL, "approved_on": "yesterday"}},
    {"bland_first_paid_call_approval": {**APPROVAL, "reference": " "}},
    {"bland_first_paid_call_approval": {**APPROVAL, "extra": "x"}},
    {"bland_first_paid_call_approval": True},
])
def test_bad_config_fails_closed(tmp_path, doc):
    with pytest.raises(ValueError):
        parse_config(doc)
    path = tmp_path / "execution.json"
    path.write_text(json.dumps(doc))
    c = load_config(str(path))
    assert c.error and not any(c.is_live(e) for e in ("browser", "bland_call", "email"))
    assert not c.paid_call_approved()


def test_unreadable_or_missing_config_fails_closed(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    for p in (tmp_path / "bad.json", tmp_path / "missing.json"):
        c = load_config(str(p))
        assert c.error and not c.is_live("browser")


def test_valid_config_parses():
    c = parse_config({"version": 1, "live": {"browser": True}, "bland_first_paid_call_approval": APPROVAL})
    assert c.is_live("browser") and not c.is_live("email") and c.paid_call_approved()


# -- default gate: nothing live -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("executor", ["browser", "bland_call", "email"])
def test_default_gate_never_starts_a_live_run(store, clock, executor):
    store.update_user({"approved_pii_fields": ["name"]})
    save_call_identity(store, "Alex")
    gate = ActionGate(store, now_fn=clock)  # the real guard, reading the repo config
    s = sub(store)
    extra = {}
    if executor == "bland_call":
        extra = {"constraints": {"subscription_id": s["id"], "call_user_name": "Alex"},
                 "draft_content": build_call_script("Alex", cancellation.call_task_steps("Netflix"))}
    elif executor == "email":
        extra = {"draft_content": cancellation.email_draft("Netflix", None)}
    req = confirm(gate, gate.create("cancel_subscription", s["id"], "Cancel Netflix", ["Cancel"], executor=executor,
                                    **extra))
    with pytest.raises(LiveExecutionDisabledError):
        gate.begin_execution(req["id"], executor)
    assert store.find("action_runs") == [] and store.find("live_runs") == []
    assert gate.get(req["id"])["status"] == "approved"


def test_manual_runs_are_not_live_and_not_blocked(store, clock):
    gate = ActionGate(store, now_fn=clock)
    s = sub(store)
    req = confirm(gate, cancel_req(gate, store, gate.live_guard, s, "manual"))
    assert "first_live_cancel_merchant" not in req["constraints"]
    assert gate.begin_execution(req["id"], "manual")
    assert store.find("live_runs") == []


def test_live_on_but_unverified_registry_is_blocked(store, clock):
    guard = LiveGuard(cfg(browser=True), registry(verified=False))
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    s = sub(store)
    req = confirm(gate, cancel_req(gate, store, guard, s))
    with pytest.raises(UnreviewedTargetError):
        gate.begin_execution(req["id"], "browser")


def test_unknown_merchant_is_blocked_even_when_live(store, clock):
    guard = LiveGuard(cfg(browser=True), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    s = sub(store, mid=None, name="Some Gym")
    req = confirm(gate, gate.create("cancel_subscription", s["id"], "Cancel Some Gym", ["Cancel"], executor="browser",
                                    constraints={"cancel_target": "https://gym.example/cancel"}))
    with pytest.raises(UnreviewedTargetError):
        gate.begin_execution(req["id"], "browser")


def test_target_must_match_the_registry(store, clock):
    guard = LiveGuard(cfg(browser=True), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    s = sub(store)
    for target in (None, "https://evil.example/netflix-cancel"):
        c = {"subscription_id": s["id"], **({"cancel_target": target} if target else {})}
        req = confirm(gate, gate.create("cancel_subscription", s["id"], "Cancel Netflix", ["Cancel"],
                                        executor="browser", constraints=c))
        with pytest.raises(UnreviewedTargetError, match="cancel_target"):
            gate.begin_execution(req["id"], "browser")


# -- D34 first live cancellation per merchant ------------------------------------------------------------------------
def test_first_live_cancel_is_shown_confirmed_alone_and_recorded(store, clock):
    guard = LiveGuard(cfg(browser=True), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    s = sub(store)
    req = cancel_req(gate, store, guard, s)
    assert req["constraints"]["first_live_cancel_merchant"] == "netflix"
    assert req["constraints"]["cancel_target"] == "https://www.netflix.com/cancelplan"
    assert "First real cancellation with netflix" in req["confirmation_text"]
    assert "https://www.netflix.com/cancelplan" in req["confirmation_text"]
    with pytest.raises(GateError, match="own confirmation"):
        gate.confirm_batch([req["id"]], f"confirm {req['confirmation_code']}")
    req = confirm(gate, req)
    run = gate.begin_execution(req["id"], "browser")
    [lv] = store.find("live_runs")
    assert (lv["run_id"], lv["executor"], lv["merchant_id"]) == (run["id"], "browser", "netflix")
    from core.evidence import EvidenceStore
    ev = EvidenceStore(store, str(store.path) + ".ev").add_ref("call_recording_url", "ref:1", s["id"])
    gate.finish_execution(run["id"], "success", [ev["id"]])
    # after one live success the next Netflix cancel is no longer a first
    s2 = sub(store)
    req2 = cancel_req(gate, store, guard, s2)
    assert "first_live_cancel_merchant" not in req2["constraints"]
    assert "First real cancellation" not in req2["confirmation_text"]


def test_first_live_flag_cannot_be_forged_or_dropped(store, clock):
    guard = LiveGuard(cfg(browser=True), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    s = sub(store)
    plan = cancellation.plan_cancellation(store, guard, s["id"], "browser")
    c = {**plan["constraints"], "first_live_cancel_merchant": "someone_else"}
    req = gate.create("cancel_subscription", s["id"], "Cancel", ["Cancel"], executor="browser", constraints=c)
    assert req["constraints"]["first_live_cancel_merchant"] == "netflix"
    # a request without the flag (e.g. made before D34) is refused before any live run
    probe = {**req, "constraints": {k: v for k, v in req["constraints"].items() if k != "first_live_cancel_merchant"}}
    assert "first_live_needs_user_approval" in {b["code"] for b in guard.preflight(store, probe)}
    with pytest.raises(FirstLiveApprovalRequiredError):
        guard.check(store, probe)
    # amending to manual drops the flag; back to browser adds it again (and needs a new confirmation)
    m = gate.amend(req["id"], {"executor": "manual"}, "user prefers to do it")
    assert "first_live_cancel_merchant" not in m["constraints"] and m["status"] == "pending_user"
    b = gate.amend(req["id"], {"executor": "browser"}, "back to the browser")
    assert b["constraints"]["first_live_cancel_merchant"] == "netflix"


# -- D35 first paid Bland call ------------------------------------------------------------------------------------
def call_probe(store, s, target="+18005550100"):
    return {"id": "x", "action_type": "cancel_subscription", "target_ref": s["id"], "executor": "bland_call",
            "constraints": {"subscription_id": s["id"], "cancel_target": target,
                            "first_live_cancel_merchant": "acme"}}


def test_first_paid_call_needs_charles(store):
    s = sub(store, mid="acme", name="Acme Phone Co")
    guard = LiveGuard(cfg(bland_call=True), registry())
    assert [b["code"] for b in guard.preflight(store, call_probe(store, s))] == ["paid_call_needs_charles"]
    with pytest.raises(PaidCallApprovalRequiredError):
        guard.check(store, call_probe(store, s))
    ok = LiveGuard(cfg(approval=APPROVAL, bland_call=True), registry())
    assert ok.preflight(store, call_probe(store, s)) == []
    assert ok.status(store) == {"live": {"browser": False, "bland_call": True, "email": False}, "config_error": None,
                                "first_paid_bland_call_approved": True, "live_bland_call_placed": False}


def test_bland_call_through_the_gate_records_the_live_call(store, clock):
    store.update_user({"approved_pii_fields": ["name"]})
    save_call_identity(store, "Alex")
    guard = LiveGuard(cfg(approval=APPROVAL, bland_call=True), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    s = sub(store, mid="acme", name="Acme Phone Co")
    plan = cancellation.plan_cancellation(store, guard, s["id"], "bland_call")
    assert plan["cancel_target"] == "+18005550100" and plan["live_blockers"] == []
    req = gate.create("cancel_subscription", s["id"], "Call Acme to cancel", plan["plan_steps"], executor="bland_call",
                      constraints={**plan["constraints"], "call_user_name": "Alex"},
                      draft_content=build_call_script("Alex", plan["call_task_steps"]))
    req = confirm(gate, req)
    gate.begin_execution(req["id"], "bland_call")
    assert guard.status(store)["live_bland_call_placed"] is True
    # once a live call exists, a later call no longer needs the one-off approval
    later = LiveGuard(cfg(bland_call=True), registry())
    assert "paid_call_needs_charles" not in {b["code"] for b in later.preflight(store, call_probe(store, s))}


# -- Q11 / D32: proposals never drive a live cancellation -------------------------------------------------------
def test_proposals_never_become_live_targets(store, clock):
    reg = registry()
    reg["netflix"]["cancel_url"] = None  # the reviewed list has no link yet
    guard = LiveGuard(cfg(browser=True, bland_call=True, email=True, approval=APPROVAL), reg)
    props = MerchantProposals(store, reg)
    props.record(merchant_id="netflix", method="web", cancel_url="https://www.netflix.com/cancelplan",
                 support_phone="+18005551234", cancel_email="cancel@netflix.example", steps=["Account", "Cancel"])
    s = sub(store)
    for executor in ("browser", "bland_call", "email"):
        plan = cancellation.plan_cancellation(store, guard, s["id"], executor)
        assert plan["cancel_target"] is None and "cancel_target" not in plan["constraints"]
        assert "unreviewed_target" in {b["code"] for b in plan["live_blockers"]}
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    req = confirm(gate, gate.create("cancel_subscription", s["id"], "Cancel", ["Cancel"], executor="browser",
                                    constraints={"subscription_id": s["id"],
                                                 "cancel_target": "https://www.netflix.com/cancelplan"}))
    with pytest.raises(UnreviewedTargetError):
        gate.begin_execution(req["id"], "browser")


def test_execution_code_never_reads_the_proposals_table():
    for rel in ("core/execution_policy.py", "core/action_gate.py", "modules/cancellation.py", "modules/trials.py",
                "core/merchant_resolver.py"):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            src = fh.read()
        assert "merchant_method_proposals\"" not in src and "merchant_proposals import" not in src, rel
        assert "FROM merchant_method_proposals" not in src, rel


def test_proposal_validation_dedupe_export_and_withdraw(store):
    reg = registry()
    props = MerchantProposals(store, reg)
    for bad in ({"method": "web"}, {"method": "fax", "merchant_name": "X", "steps": ["a"]},
                {"method": "web", "merchant_name": "X", "cancel_url": "http://x.example"},
                {"method": "phone", "merchant_name": "X", "support_phone": "800-555-0100"},
                {"method": "email", "merchant_name": "X", "cancel_email": "nope"},
                {"method": "web", "merchant_name": "X", "steps": ["x" * 301]},
                {"method": "web", "merchant_name": "X", "steps": ["s"] * 21},
                {"method": "web", "merchant_id": "not_a_merchant", "steps": ["a"]},
                {"method": "web", "merchant_name": "X", "steps": ["a"], "source_urls": ["ftp://x"]},
                {"method": "web", "merchant_name": "X", "steps": ["a"], "password": "hunter2"}):
        with pytest.raises((ProposalError, TypeError)):
            props.record(**bad)
    a = props.record(merchant_id="netflix", method="web", cancel_url="https://www.netflix.com/account/cancel",
                     source_urls=["https://help.netflix.com/en/node/407"])
    assert a["merchant_name"] == "Netflix" and a["status"] == "proposed" and a["duplicate"] is False
    assert props.record(merchant_id="netflix", method="web",
                        cancel_url="https://www.netflix.com/account/cancel")["duplicate"] is True
    b = props.record(merchant_name="Tiny Gym", method="phone", support_phone="+14155550123", steps=["Call", "Ask"])
    assert [p["id"] for p in props.list()] == [a["id"], b["id"]]
    props.withdraw(b["id"])
    doc = props.export()
    assert doc["count"] == 1 and "Reviewer merges" in doc["review_rule"]
    item = doc["proposals"][0]
    assert item["proposed"]["cancel_url"] == "https://www.netflix.com/account/cancel"
    assert item["registry_now"]["cancel_url"] == "https://www.netflix.com/cancelplan"
    assert props.list(status="exported")[0]["id"] == a["id"]
    with pytest.raises(ProposalError):
        props.export([b["id"]])
    with pytest.raises(ProposalError):
        props.export(["mmp_missing"])
    # the reviewed registry itself is untouched
    assert load_registry()["netflix"]["cancel_url"] == "https://www.netflix.com/cancelplan"


def test_proposals_from_run_log(store):
    props = MerchantProposals(store, registry())
    saved, errors = props.record_from_log([
        "plain text", {"step": 1},
        {"learned_method": {"merchant_id": "netflix", "method": "web", "steps": ["Account", "Cancel membership"]}},
        {"learned_method": {"merchant_id": "netflix", "method": "phone", "support_phone": "12345"}},
        {"learned_method": {"merchant_id": "netflix", "method": "web", "bogus": 1}}], "run_1")
    assert len(saved) == 1 and saved[0]["observed_ref"] == "run_1" and len(errors) == 2


# -- planning, dry run and the manual guide ---------------------------------------------------------------------
def test_plan_for_each_channel(store):
    guard = LiveGuard(cfg(), registry())
    s = sub(store, mid="acme", name="Acme Phone Co")
    email = cancellation.plan_cancellation(store, guard, s["id"], "email")
    assert email["cancel_target"] == "cancel@acme.example" and "alex@example.com" in email["draft_content"]
    assert "live_disabled" in {b["code"] for b in email["live_blockers"]}
    assert "approve the exact email" in " ".join(email["plan_steps"])
    call = cancellation.plan_cancellation(store, guard, s["id"], "bland_call")
    script = build_call_script("Alex", call["call_task_steps"])
    assert "AI assistant" in script
    assert {"live_disabled", "paid_call_needs_charles"} <= {b["code"] for b in call["live_blockers"]}
    zh = cancellation.plan_cancellation(store, guard, s["id"], "email", lang="zh")
    assert "请取消" in zh["draft_content"]
    with pytest.raises(cancellation.CancellationError):
        cancellation.plan_cancellation(store, guard, s["id"], "fax")
    with pytest.raises(cancellation.CancellationError):
        cancellation.plan_cancellation(store, guard, "sub_missing", "browser")


def test_app_store_billing_is_manual_only(store):
    guard = LiveGuard(cfg(browser=True), registry())
    s = sub(store, channel="apple")
    plan = cancellation.plan_cancellation(store, guard, s["id"], "browser")
    assert plan["executor"] == "manual" and plan["live_blockers"] == []
    g = plan["manual_guide"]
    assert g["link"] == "https://apps.apple.com/account/subscriptions"
    assert "billed through Apple App Store" in g["steps"][0]


def test_manual_guide(store):
    s = sub(store)
    g = cancellation.manual_guide(store, registry(verified=False), s["id"])
    assert g["link"] == "https://www.netflix.com/cancelplan" and g["phone"] is None
    assert any("not verified" in n for n in g["notes"])
    assert "Netflix" in g["talking_points"][0]
    unknown = sub(store, mid=None, name="Some Gym")
    g2 = cancellation.manual_guide(store, registry(), unknown["id"], "zh")
    assert g2["link"] is None and "官方" not in g2["steps"][0] and "确认" in g2["talking_points"][2]


def test_dry_run_starts_nothing(store, clock):
    guard = LiveGuard(cfg(), registry())
    gate = ActionGate(store, now_fn=clock, live_guard=guard)
    s = sub(store)
    req = confirm(gate, cancel_req(gate, store, guard, s))
    d = cancellation.dry_run(store, guard, req)
    assert d["approved"] and d["would_execute"]["cancel_target"] == "https://www.netflix.com/cancelplan"
    assert [b["code"] for b in d["live_blockers"]] == ["live_disabled"]
    assert store.find("action_runs") == []


def test_card_lines_in_both_languages():
    req = {"id": "ar_1", "summary": "Cancel Netflix", "plan_steps": ["Cancel"], "executor": "browser", "disclosed_fields": [],
           "constraints": {"first_live_cancel_merchant": "netflix", "cancel_target": "https://www.netflix.com/x"},
           "expected_saving_cents": None, "risk_notes": "", "expires_at": "2026-10-23T00:00:00+00:00",
           "confirmation_code": "AB12", "draft_content": None, "action_type": "cancel_subscription"}
    en = notifier.format_action_request(req)
    zh = notifier.format_action_request(req, "zh")
    assert "First real cancellation with netflix" in en and "https://www.netflix.com/x" in en
    assert "第一次真实地为你取消 netflix" in zh


def test_delete_all_data_covers_the_new_tables(store):
    MerchantProposals(store, registry()).record(merchant_name="Tiny Gym", method="web", steps=["Cancel"])
    counts = store.wipe_all()
    assert counts["merchant_method_proposals"] == 1 and "live_runs" in counts


def test_app_tools_wire_the_guard_and_proposals(tmp_path):
    from server.server import TOOLS, App
    names = {t["name"] for t in TOOLS}
    assert {"execution_status", "plan_cancellation", "dry_run_action_request", "manual_cancel_guide",
            "record_merchant_method_proposal", "list_merchant_method_proposals", "export_merchant_method_proposals",
            "withdraw_merchant_method_proposal"} <= names
    app = App(str(tmp_path / "msb.db"), str(tmp_path / "ev"), [str(tmp_path / "inbox")])
    assert app.call("execution_status", {})["live"] == {"browser": False, "bland_call": False, "email": False}
    s = app.call("add_subscription", {"display_name": "Netflix", "amount_cents": 1799, "period": "monthly",
                                      "merchant_id": "netflix"})
    sid = (s.get("subscription") or s)["id"]
    plan = app.call("plan_cancellation", {"subscription_id": sid, "executor": "browser"})
    # the shipped registry entry is merged but still "unverified", so it can't drive a live run yet (D36)
    assert plan["cancel_target"] == "https://www.netflix.com/cancelplan"
    assert {"live_disabled", "unreviewed_target"} <= {b["code"] for b in plan["live_blockers"]}
    assert app.call("manual_cancel_guide", {"subscription_id": sid})["link"] == "https://www.netflix.com/cancelplan"
    made = app.call("create_action_request", {"action_type": "cancel_subscription", "target_ref": sid,
                                              "summary": "Cancel Netflix", "plan_steps": plan["manual_guide"]["steps"],
                                              "executor": "manual", "constraints": plan["constraints"]})
    rid = made["request"]["id"]
    app.call("confirm_action_request", {"request_id": rid, "user_message": f"confirm {made['confirmation_code']}"})
    run = app.call("start_action_run", {"request_id": rid, "executor": "manual"})["run"]
    out = app.call("finish_action_run", {"run_id": run["id"], "outcome": "needs_user", "log": [
        {"learned_method": {"merchant_id": "netflix", "method": "web", "steps": ["Account", "Cancel membership"]}}]})
    assert out["proposals_recorded"][0]["observed_ref"] == run["id"] and out["proposal_errors"] == []
    p = app.call("record_merchant_method_proposal", {"merchant_name": "Tiny Gym", "method": "email",
                                                     "cancel_email": "billing@tinygym.example"})["proposal"]
    assert len(app.call("list_merchant_method_proposals", {"status": "proposed"})["proposals"]) == 2
    app.call("withdraw_merchant_method_proposal", {"proposal_id": p["id"]})
    assert app.call("export_merchant_method_proposals", {})["count"] == 1
    assert app.call("dry_run_action_request", {"request_id": rid})["live_blockers"] == []
