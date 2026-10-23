"""Confirmation gate state machine and negative tests (M0 acceptance: unconfirmed actions cannot execute)."""
from datetime import timedelta

import pytest

from core.action_gate import (ALLOWED_EXECUTORS, ActionGate, ActionNotAllowedError, GateError,
                              InvalidTransitionError, NotApprovedError, NotExplicitConfirmationError,
                              is_explicit_confirmation, sha256_text)
from core.call_policy import build_call_script, save_call_identity
from core.evidence import EvidenceStore
from core.privacy import ForbiddenDataError
from core.store import Store
from executors.base import GuardedExecutor

from conftest import GateMechanicsGuard

DRAFT = "Hello, I am an AI assistant calling on behalf of Alex to cancel the subscription."
# Call scripts must be built by core.call_policy (D30) for the name in constraints.call_user_name.
CALL_DRAFT = build_call_script("Alex", ["Ask to cancel the subscription on this account.",
                                        "Ask for a cancellation confirmation number."])
CALL_C = {"call_user_name": "Alex"}


def make(gate, **kw):
    args = dict(action_type="cancel_subscription", target_ref="sub_hulu", summary="Cancel Hulu ($17.99/month) on the web",
                plan_steps=["Open hulu.com account page", "Click Cancel", "Decline retention offers", "Screenshot confirmation"],
                expected_saving_cents=1799, risk_notes="Access continues until 10/24")
    args.update(kw)
    executor = args.get("executor") or (ALLOWED_EXECUTORS.get(args["action_type"]) or ("",))[0]
    if executor == "bland_call":
        save_call_identity(gate.store, "Alex")   # the gate binds call scripts to the user's saved name
    if executor == "bland_call" and args.get("draft_content") == DRAFT:
        args["draft_content"] = CALL_DRAFT
        args["constraints"] = {**CALL_C, **(args.get("constraints") or {})}
    return gate.create(**args)


def ok(gate, req, word="confirm"):
    return gate.confirm(req["id"], f"{word} {req['confirmation_code']}")


def evidence_id(store):
    return EvidenceStore(store, root="/tmp/unused").add_email("m-confirm-1", "sub_hulu", "Your cancellation is confirmed")["id"]


# --- explicit confirmation (8.2): allow-list only, fail closed -------------------------------------
@pytest.mark.parametrize("msg", ["Confirm", "confirm.", "Yes, confirm", "I confirm!", "approve", "Approved",
                                 "确认", "确认。", "我确认", "同意", "批准", "确认执行", "Confirm all", "全部确认"])
def test_allowlisted_affirmatives(msg):
    assert is_explicit_confirmation(msg)


@pytest.mark.parametrize("msg", ["confirm ABCDEF", "ABCDEF confirm", "Confirm: ABCDEF", "确认 ABCDEF", "确认ABCDEF",
                                 "批准 abcdef"])
def test_affirmative_with_code(msg):
    assert is_explicit_confirmation(msg, ["ABCDEF"])


NEGATIVE_REPLIES = [
    # reviewer's reproductions (PR #1 review)
    "I can't confirm that", "I won't approve this", "I refuse to authorize this", "我不批准", "拒绝批准", "没确认",
    "我没说确认", "Do it later, I haven't decided",
    # negations and refusals
    "no", "No, don't confirm", "don't cancel", "do not approve", "I cannot confirm", "I wouldn't approve that",
    "I haven't confirmed", "never confirm", "not confirmed", "I do not confirm", "reject", "denied", "nope",
    "不要", "不确认", "先不确认", "不同意", "不执行", "别确认", "暂不批准", "取消", "我拒绝", "没有批准", "确认不了",
    # questions
    "confirm?", "Yes?", "确认？", "should I confirm", "can you confirm first?", "approve？",
    # conditionals and hedges
    "confirm if it's free", "if you think so, confirm", "confirm unless there's a fee", "maybe", "I guess so",
    "probably yes", "whatever", "sure, whatever", "好像可以", "随便", "也许吧", "如果免费就确认", "要是没费用就确认",
    "confirm later", "wait, confirm later", "not now", "sounds interesting",
    # extra content / wrong target / not a confirmation
    "Yes, cancel Hulu", "confirm cancelling Hulu and Netflix", "确认取消 Hulu", "go ahead", "do it", "proceed",
    "ok", "OK!", "sure", "yes", "y", "👍", "I confirmed yesterday", "confirm confirm confirm but not this one",
    "Yes, confirm the phone call", "Confirm both",
    # empty / silence / non-text
    "", "   ", None, 123,
]


@pytest.mark.parametrize("msg", NEGATIVE_REPLIES)
def test_negative_vague_or_extra_replies_are_not_confirmation(msg):
    assert not is_explicit_confirmation(msg)
    assert not is_explicit_confirmation(msg, ["ABCDEF"])


@pytest.mark.parametrize("msg", ["confirm XYZXYZ", "confirm ABCDEF XYZXYZ", "confirm ABCDEFG", "don't confirm ABCDEF",
                                 "ABCDEF?", "不确认 ABCDEF", "confirm ABCDEF if free"])
def test_wrong_or_decorated_codes_fail(msg):
    assert not is_explicit_confirmation(msg, ["ABCDEF"])


@pytest.mark.parametrize("msg", ["I can't confirm that", "我不批准", "maybe", "confirm?", "yes"])
def test_gate_rejects_negative_replies_and_stays_pending(gate, msg):
    req = make(gate)
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(req["id"], msg, shown_text_sha256=req["confirmation_sha256"])
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(req["id"], f"{msg} {req['confirmation_code']}")
    assert gate.get(req["id"])["status"] == "pending_user"
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


# --- binding approval to the confirmation text shown -------------------------------------------------
def test_request_stores_text_code_and_digest(gate):
    req = make(gate)
    assert len(req["confirmation_code"]) == 6 and req["confirmation_code"] in req["confirmation_text"]
    assert req["confirmation_sha256"] == sha256_text(req["confirmation_text"])
    assert "Cancel Hulu" in req["confirmation_text"] and "web browser" in req["confirmation_text"]


def test_bare_confirm_needs_matching_text_digest(gate):
    req = make(gate)
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(req["id"], "confirm")
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(req["id"], "confirm", shown_text_sha256=sha256_text("some other text"))
    approved = gate.confirm(req["id"], "Confirm", shown_text_sha256=req["confirmation_sha256"], source="chat:msg-42")
    assert approved["status"] == "approved" and approved["approval_source"] == "chat:msg-42"


def test_code_of_another_request_does_not_approve(gate):
    a, b = make(gate), make(gate, target_ref="sub_other")
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(a["id"], f"confirm {b['confirmation_code']}")


def test_amend_invalidates_old_code_and_digest(gate):
    req = make(gate)
    amended = gate.amend(req["id"], {"risk_notes": "Early termination fee $25"}, "fee discovered")
    assert amended["confirmation_code"] != req["confirmation_code"]
    assert "Early termination fee $25" in amended["confirmation_text"]
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(req["id"], f"confirm {req['confirmation_code']}")
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(req["id"], "confirm", shown_text_sha256=req["confirmation_sha256"])
    assert ok(gate, amended)["status"] == "approved"


# --- happy path ----------------------------------------------------------------------------------
def test_full_lifecycle_pending_to_executed(gate, store):
    req = make(gate)
    assert req["status"] == "pending_user" and req["executor"] == "browser"
    req = ok(gate, req)
    assert req["status"] == "approved" and req["approved_by_message"].startswith("confirm ")
    run = gate.begin_execution(req["id"], "browser")
    run = gate.finish_execution(run["id"], "success", [evidence_id(store)])
    req = gate.get(req["id"])
    assert req["status"] == "executed"
    assert [h["to"] for h in req["history"]] == ["pending_user", "approved", "executed"]
    assert run["outcome"] == "success" and run["finished_at"]


def test_chinese_confirmation(gate):
    assert ok(gate, make(gate), "确认")["status"] == "approved"


def test_failed_run_marks_request_failed(gate):
    req = ok(gate, make(gate))
    run = gate.begin_execution(req["id"], "browser")
    gate.finish_execution(run["id"], "failed", error_code="playbook_broken")
    assert gate.get(req["id"])["status"] == "failed"


def test_needs_user_keeps_run_open_and_resumes(gate, store):
    req = ok(gate, make(gate))
    run = gate.begin_execution(req["id"], "browser")
    gate.finish_execution(run["id"], "needs_user", error_code="login_required")
    assert gate.get(req["id"])["status"] == "approved"
    resumed = gate.begin_execution(req["id"], "browser")
    assert resumed["id"] == run["id"]
    gate.finish_execution(run["id"], "success", [evidence_id(store)])
    assert gate.get(req["id"])["status"] == "executed"


# --- negative: nothing executes without explicit approval ----------------------------------------
def test_pending_request_cannot_execute(gate):
    req = make(gate)
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    assert gate.store.count("action_runs") == 0


def test_unknown_request_cannot_execute(gate):
    with pytest.raises(NotApprovedError):
        gate.begin_execution("ar_doesnotexist", "browser")


@pytest.mark.parametrize("terminal", ["reject", "cancel_by_user", "expire"])
def test_terminal_requests_cannot_execute_or_be_confirmed(gate, clock, terminal):
    req = make(gate)
    if terminal == "reject":
        gate.reject(req["id"])
    elif terminal == "cancel_by_user":
        gate.cancel_by_user(req["id"])
    else:
        clock.advance(hours=73)
        assert gate.expire_due() == [req["id"]]
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    with pytest.raises(InvalidTransitionError):
        ok(gate, req)


def test_confirming_after_expiry_fails_and_marks_expired(gate, clock):
    req = make(gate)
    clock.advance(hours=72, seconds=1)
    with pytest.raises(InvalidTransitionError):
        ok(gate, req)
    assert gate.get(req["id"])["status"] == "expired"


def test_default_expiry_is_72_hours(gate, clock):
    req = make(gate)
    clock.advance(hours=71)
    assert gate.expire_due() == []
    clock.advance(hours=1)
    assert gate.expire_due() == [req["id"]]
    assert gate.expire_due() == []  # idempotent


def test_executed_request_cannot_run_twice(gate, store):
    req = ok(gate, make(gate))
    run = gate.begin_execution(req["id"], "browser")
    gate.finish_execution(run["id"], "success", [evidence_id(store)])
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    with pytest.raises(InvalidTransitionError):
        gate.finish_execution(run["id"], "success", [evidence_id(store)])


def test_purchase_is_disabled(gate):
    with pytest.raises(ActionNotAllowedError):
        make(gate, action_type="purchase", summary="Buy TV")


def test_purchase_row_inserted_directly_still_cannot_execute(gate, store, clock):
    req = make(gate)
    store.update("action_requests", req["id"], {"action_type": "purchase", "status": "approved",
                                                "approved_by_message": "confirm",
                                                "execute_by": "2099-01-01T00:00:00Z"})
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_approved_flag_without_user_message_is_not_enough(gate, store):
    req = make(gate)
    store.update("action_requests", req["id"], {"status": "approved", "execute_by": "2099-01-01T00:00:00Z"})
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_success_requires_real_evidence(gate):
    req = ok(gate, make(gate))
    run = gate.begin_execution(req["id"], "browser")
    with pytest.raises(GateError):
        gate.finish_execution(run["id"], "success", [])
    with pytest.raises(GateError):
        gate.finish_execution(run["id"], "success", ["evd_madeup"])


def test_guarded_executor_refuses_unapproved(gate):
    calls = []

    class Spy(GuardedExecutor):
        executor_name = "browser"

        def perform(self, request, run):
            calls.append(request["id"])
            return {"outcome": "failed"}

    req = make(gate)
    with pytest.raises(NotApprovedError):
        Spy(gate).run(req["id"])
    assert calls == []


def test_guarded_executor_crash_records_failed_run(gate):
    class Boom(GuardedExecutor):
        executor_name = "browser"

        def perform(self, request, run):
            raise RuntimeError("page changed")

    req = ok(gate, make(gate))
    run = Boom(gate).run(req["id"])
    assert run["outcome"] == "failed" and run["error_code"] == "RuntimeError"
    assert gate.get(req["id"])["status"] == "failed"


# --- executor bound to action_type and to the approved channel (review non-blocking) -----------------
def test_executor_must_match_the_approved_channel(gate):
    req = ok(gate, make(gate))  # approved for browser
    for other in ("email", "bland_call", "manual"):
        with pytest.raises(NotApprovedError):
            gate.begin_execution(req["id"], other)
    assert gate.begin_execution(req["id"], "browser")


@pytest.mark.parametrize("atype,bad", [("place_call", "browser"), ("send_email", "bland_call"),
                                       ("negotiate_bill", "email"), ("rebook_travel", "bland_call"),
                                       ("submit_form_with_pii", "email")])
def test_executor_not_allowed_for_action_type(gate, atype, bad):
    with pytest.raises(GateError):
        make(gate, action_type=atype, executor=bad, draft_content=DRAFT,
             disclosed_fields=[{"field": "name", "value_masked": "Alex"}])


def test_default_executor_per_type(gate):
    for atype, allowed in ALLOWED_EXECUTORS.items():
        req = make(gate, action_type=atype, draft_content=DRAFT, target_ref=f"t_{atype}",
                   disclosed_fields=[{"field": "name", "value_masked": "Alex"}])
        assert req["executor"] == allowed[0]


def test_switching_channel_requires_reconfirmation(gate):
    save_call_identity(gate.store, "Alex")
    req = ok(gate, make(gate))
    amended = gate.amend(req["id"], {"executor": "bland_call", "draft_content": CALL_DRAFT, "constraints": CALL_C},
                         "web cancel failed")
    assert amended["status"] == "pending_user"
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "bland_call")
    ok(gate, amended)
    assert gate.begin_execution(req["id"], "bland_call")


# --- approvals expire (review non-blocking) ---------------------------------------------------------
def test_approval_expires_after_default_ttl(gate, clock):
    req = ok(gate, make(gate))
    clock.advance(hours=24, seconds=1)
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    assert gate.get(req["id"])["status"] == "expired"


def test_approval_ttl_is_configurable(store, clock):
    store.update_user({"approved_pii_fields": [], "thresholds": {"approval_ttl_hours": 2}})
    g = ActionGate(store, now_fn=clock, live_guard=GateMechanicsGuard())
    req = ok(g, make(g))
    clock.advance(hours=1)
    assert g.begin_execution(req["id"], "browser")
    req2 = ok(g, make(g, target_ref="sub_2"))
    clock.advance(hours=2, seconds=1)
    with pytest.raises(NotApprovedError):
        g.begin_execution(req2["id"], "browser")
    g3 = ActionGate(store, now_fn=clock, approval_ttl=timedelta(minutes=5))
    assert g3.approval_ttl == timedelta(minutes=5)


def test_expire_due_also_expires_stale_approvals(gate, clock):
    req = ok(gate, make(gate))
    clock.advance(hours=25)
    assert gate.expire_due() == [req["id"]]


def test_started_run_can_finish_after_deadline(gate, clock, store):
    req = ok(gate, make(gate))
    run = gate.begin_execution(req["id"], "browser")
    clock.advance(hours=30)
    gate.finish_execution(run["id"], "success", [evidence_id(store)])
    assert gate.get(req["id"])["status"] == "executed"


def test_ttl_counts_from_end_of_revoke_window(gate, clock):
    req = make(gate, execute_not_before=clock.now + timedelta(hours=10))
    ok(gate, req)
    clock.advance(hours=30)
    assert gate.begin_execution(req["id"], "browser")


# --- pre-authorization revoke window (FR-1-9, Q4) --------------------------------------------------
def test_preauthorized_request_waits_for_revoke_window(gate, clock):
    req = make(gate, execute_not_before=clock.now + timedelta(hours=2))
    ok(gate, req)
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    clock.advance(hours=2)
    assert gate.begin_execution(req["id"], "browser")["executor"] == "browser"


def test_user_can_revoke_inside_window(gate, clock):
    req = make(gate, execute_not_before=clock.now + timedelta(hours=2))
    ok(gate, req)
    gate.cancel_by_user(req["id"], "user replied stop")
    clock.advance(hours=3)
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_cannot_revoke_after_execution_started(gate):
    req = ok(gate, make(gate))
    gate.begin_execution(req["id"], "browser")
    with pytest.raises(InvalidTransitionError):
        gate.cancel_by_user(req["id"])


# --- material change requires re-confirmation (8.2) -------------------------------------------------
def test_amend_after_approval_requires_reconfirmation(gate):
    req = ok(gate, make(gate))
    req = gate.amend(req["id"], {"plan_steps": ["Open account page again", "Cancel"], "risk_notes": "retry"},
                     "page changed")
    assert req["status"] == "pending_user" and req["approved_by_message"] is None and req["execute_by"] is None
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    ok(gate, req)
    assert gate.begin_execution(req["id"], "browser")


def test_amend_rejects_status_changes(gate):
    req = make(gate)
    with pytest.raises(GateError):
        gate.amend(req["id"], {"status": "approved"}, "sneaky")
    with pytest.raises(GateError):
        gate.amend(req["id"], {"action_type": "purchase"}, "sneaky")


# --- amend runs the same validation as create (review blocking 2) ----------------------------------
AMEND_BAD = [
    ({"disclosed_fields": [{"field": "ssn", "value_masked": "123-45-6789"}]}, (GateError, ForbiddenDataError)),
    ({"disclosed_fields": [{"field": "date_of_birth", "value_masked": "**/**/1990"}]}, GateError),
    ({"disclosed_fields": [{"field": "account_last4", "value_masked": "4111 1111 1111 1111"}]}, ForbiddenDataError),
    ({"plan_steps": []}, GateError),
    ({"plan_steps": ["  "]}, GateError),
    ({"summary": ""}, GateError),
    ({"risk_notes": "SSN 123-45-6789"}, ForbiddenDataError),
    ({"plan_steps": ["type password: hunter2"]}, ForbiddenDataError),
    ({"constraints": {"card": "4111 1111 1111 1111"}}, ForbiddenDataError),
    ({"constraints": "not a dict"}, GateError),
    ({"expected_saving_cents": "lots"}, GateError),
    ({"executor": "teleport"}, GateError),
]


@pytest.mark.parametrize("changes,err", AMEND_BAD)
@pytest.mark.parametrize("approved_first", [False, True])
def test_amend_validates_like_create(gate, changes, err, approved_first):
    req = make(gate)
    if approved_first:
        req = ok(gate, req)
    before = gate.get(req["id"])
    with pytest.raises(err):
        gate.amend(req["id"], changes, "bad change")
    after = gate.get(req["id"])
    assert after["status"] == before["status"] and after["confirmation_code"] == before["confirmation_code"]


@pytest.mark.parametrize("atype", ["send_email", "place_call", "negotiate_bill"])
def test_amend_cannot_remove_required_draft(gate, atype):
    req = make(gate, action_type=atype, draft_content=DRAFT)
    for empty in ("", "   ", None):
        with pytest.raises(GateError):
            gate.amend(req["id"], {"draft_content": empty}, "drop draft")
    assert gate.get(req["id"])["draft_content"] == (CALL_DRAFT if atype != "send_email" else DRAFT)


def test_amend_cannot_switch_to_email_without_draft(gate):
    req = make(gate)
    with pytest.raises(GateError):
        gate.amend(req["id"], {"executor": "email"}, "email only merchant")


# --- batch -------------------------------------------------------------------------------------
def test_batch_confirm_all_or_nothing(gate):
    a, b, c = (make(gate, target_ref=f"sub_{i}") for i in range(3))
    gate.reject(c["id"])
    codes = " ".join(r["confirmation_code"] for r in (a, b, c))
    with pytest.raises(InvalidTransitionError):
        gate.confirm_batch([a["id"], b["id"], c["id"]], f"confirm {codes}")
    assert gate.get(a["id"])["status"] == "pending_user"
    approved = gate.confirm_batch([a["id"], b["id"]], f"confirm {a['confirmation_code']} {b['confirmation_code']}")
    assert all(r["status"] == "approved" and "batch[" in r["approved_by_message"] for r in approved)


def test_batch_with_batch_text_digest(gate):
    a, b = make(gate, target_ref="s1"), make(gate, target_ref="s2")
    text = gate.batch_text([a["id"], b["id"]])
    assert a["confirmation_code"] in text and "1. [" in text and "2. [" in text
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm_batch([a["id"], b["id"]], "confirm all")
    approved = gate.confirm_batch([a["id"], b["id"]], "Confirm all", shown_text_sha256=sha256_text(text))
    assert len(approved) == 2


def test_batch_needs_every_code(gate):
    a, b = make(gate, target_ref="s1"), make(gate, target_ref="s2")
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm_batch([a["id"], b["id"]], f"confirm {a['confirmation_code']}")


@pytest.mark.parametrize("msg", ["sure, whatever", "confirm all?", "I can't confirm these", "全部不批准"])
def test_batch_rejects_non_affirmative(gate, msg):
    a = make(gate)
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm_batch([a["id"]], msg, shown_text_sha256=sha256_text(gate.batch_text([a["id"]])))


# --- creation validation -------------------------------------------------------------------------
def test_disclosed_fields_must_be_preapproved(gate):
    with pytest.raises(GateError):
        make(gate, disclosed_fields=[{"field": "date_of_birth", "value_masked": "**/**/1990"}])
    req = make(gate, disclosed_fields=[{"field": "account_last4", "value_masked": "****4821"}])
    assert req["disclosed_fields"][0]["field"] == "account_last4"
    assert "account_last4=****4821" in req["confirmation_text"]


def test_disclosed_value_cannot_contain_full_card(gate):
    with pytest.raises(ForbiddenDataError):
        make(gate, disclosed_fields=[{"field": "account_last4", "value_masked": "4111 1111 1111 1111"}])


@pytest.mark.parametrize("atype", ["send_email", "place_call", "negotiate_bill"])
def test_email_and_calls_need_a_draft(gate, atype):
    with pytest.raises(GateError):
        make(gate, action_type=atype)
    req = make(gate, action_type=atype, draft_content=DRAFT)
    for line in req["draft_content"].splitlines():  # the user sees the exact text
        assert f"| {line}" in req["confirmation_text"]


def test_cancel_via_email_needs_a_draft(gate):
    with pytest.raises(GateError):
        make(gate, executor="email")


def test_plan_steps_required(gate):
    with pytest.raises(GateError):
        make(gate, plan_steps=[])


def test_timeline_lists_requests_with_runs(gate, store):
    r1 = ok(gate, make(gate))
    run = gate.begin_execution(r1["id"], "browser")
    gate.finish_execution(run["id"], "success", [evidence_id(store)])
    make(gate, target_ref="sub_other")
    tl = gate.timeline()
    assert len(tl) == 2
    assert {t["request"]["id"]: (t["run"] or {}).get("outcome") for t in tl}[r1["id"]] == "success"


# --- regression: bounded TTLs and dates, clean errors instead of OverflowError (re-review 2 and 3) ----
from datetime import datetime, timezone  # noqa: E402


@pytest.mark.parametrize("hours", [100000, 1e9, 72.01, 0, -1, float("inf"), float("nan"), "24", True, None])
def test_approval_ttl_setting_is_validated(store, hours):
    with pytest.raises(ValueError):
        store.update_user({"thresholds": {"approval_ttl_hours": hours}})


def test_unknown_or_bad_thresholds_rejected_and_others_kept(store):
    with pytest.raises(ValueError):
        store.update_user({"thresholds": {"approval_ttl_days": 1}})
    with pytest.raises(ValueError):
        store.update_user({"thresholds": "lots"})
    store.update_user({"thresholds": {"approval_ttl_hours": 72}})
    t = store.get_user()["thresholds"]
    assert t["approval_ttl_hours"] == 72 and t["price_drop_min_cents"] == 500  # merged, not replaced


def test_forged_huge_ttl_in_db_is_capped(store, clock):
    store.update_user({"approved_pii_fields": []})
    user = store.get_user()
    store.update("users", user["id"], {"thresholds": {"approval_ttl_hours": 1e9}})
    g = ActionGate(store, now_fn=clock)
    assert g.approval_ttl == timedelta(hours=72)
    req = ok(g, make(g))  # no OverflowError
    assert req["execute_by"] == "2026-10-22T20:00:00Z"
    clock.advance(hours=72, seconds=1)
    with pytest.raises(NotApprovedError):
        g.begin_execution(req["id"], "browser")


@pytest.mark.parametrize("bad", [-5, "abc", True])
def test_forged_invalid_ttl_falls_back_to_default(store, clock, bad):
    store.update("users", store.get_user()["id"], {"thresholds": {"approval_ttl_hours": bad}})
    assert ActionGate(store, now_fn=clock).approval_ttl == timedelta(hours=24)


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(hours=73), timedelta(days=-1)])
def test_constructor_ttl_bounds(store, clock, ttl):
    with pytest.raises(ValueError):
        ActionGate(store, now_fn=clock, approval_ttl=ttl)


def test_pending_expiry_is_capped_at_72_hours(gate, clock):
    with pytest.raises(GateError):
        make(gate, expires_at=datetime(9999, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(GateError):
        make(gate, expires_at=clock.now + timedelta(hours=72, seconds=1))
    assert make(gate, expires_at=clock.now + timedelta(hours=72))["expires_at"] == "2026-10-22T20:00:00Z"
    assert make(gate, expires_at=clock.now + timedelta(hours=1))


@pytest.mark.parametrize("nb", [datetime(9999, 12, 31, tzinfo=timezone.utc), datetime(2026, 12, 4, 20, 0, 1, tzinfo=timezone.utc),
                                datetime(2026, 10, 18, tzinfo=timezone.utc)])
def test_execute_not_before_range(gate, nb):
    with pytest.raises(GateError):
        make(gate, execute_not_before=nb)


def test_naive_datetimes_rejected(gate):
    with pytest.raises(GateError):
        make(gate, expires_at=datetime(2026, 10, 20))


def test_forged_far_future_dates_fail_closed_without_overflow(gate, store):
    req = make(gate)
    store.update("action_requests", req["id"], {"execute_not_before": "9999-12-31T00:00:00Z"})
    with pytest.raises(GateError):  # clean GateError, not OverflowError
        ok(gate, req)
    req2 = ok(gate, make(gate, target_ref="s2"))
    store.update("action_requests", req2["id"], {"execute_by": "9999-12-31T23:59:59Z"})
    with pytest.raises(NotApprovedError):  # deadline beyond the cap is treated as lapsed
        gate.begin_execution(req2["id"], "browser")
    req3 = ok(gate, make(gate, target_ref="s3"))
    store.update("action_requests", req3["id"], {"execute_by": "not-a-date"})
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req3["id"], "browser")


def test_preauth_within_45_days_works(gate, clock):
    req = make(gate, execute_not_before=clock.now + timedelta(days=30))
    ok(gate, req)
    clock.advance(days=30)
    assert gate.begin_execution(req["id"], "browser")


# --- re-review 6: the code is a separate token and the affirmative is contiguous in the original reply ---

SPLIT_AFFIRMATIVE = [
    "批 ABCDEF 准", "批ABCDEF准", "确 ABCDEF 认", "确ABCDEF认", "同 ABCDEF 意", "我 ABCDEF 批准",
    "批 准", "确 认", "批 准 ABCDEF", "确 认 ABCDEF", "ABCDEF 批 准",
    "confirm ABCDEF all", "i ABCDEF confirm", "yes ABCDEF confirm", "con ABCDEF firm", "conABCDEFfirm",
    "确认ABCDEF执行", "确认 ABCDEF 全部", "ABCDEF confirm ABCDEF", "ABCDEF 确认 ABCDEF",
    "confirmABCDEF", "ABCDEFconfirm", "confirm ABCDEFG", "confirm XABCDEF", "confirm ABCDE",
    "confirm ABCDEF ZZZZZZ", "确认 ABCDEF 不",
]


@pytest.mark.parametrize("reply", SPLIT_AFFIRMATIVE)
def test_code_must_not_split_or_join_the_affirmative(reply):
    assert not is_explicit_confirmation(reply, ["ABCDEF"])


@pytest.mark.parametrize("reply", [
    "确认ABCDEF", "确认 ABCDEF", "ABCDEF确认", "ABCDEF 确认", "批准 ABCDEF", "批准ABCDEF", "我确认：ABCDEF",
    "确认执行 ABCDEF", "confirm ABCDEF", "ABCDEF confirm", "Confirm: ABCDEF", "I confirm ABCDEF.", "批准 abcdef",
])
def test_code_as_separate_token_still_confirms(reply):
    assert is_explicit_confirmation(reply, ["ABCDEF"])


def test_split_chinese_affirmative_cannot_approve_a_request(gate):
    req = gate.create(action_type="cancel_subscription", target_ref="sub_x", summary="Cancel X", plan_steps=["a"])
    code = req["confirmation_code"]
    for msg in (f"批 {code} 准", f"确 {code} 认", f"批{code}准", "批 准"):
        with pytest.raises(NotExplicitConfirmationError):
            gate.confirm(req["id"], msg, shown_text_sha256=req["confirmation_sha256"])
    assert gate.get(req["id"])["status"] == "pending_user"
    assert gate.confirm(req["id"], f"批准 {code}")["status"] == "approved"


def test_batch_codes_must_be_one_block_at_an_end():
    codes = ["AAAAAA", "BBBBBB"]
    assert is_explicit_confirmation("confirm AAAAAA BBBBBB", codes)
    assert is_explicit_confirmation("AAAAAA BBBBBB 全部确认", codes)
    assert not is_explicit_confirmation("AAAAAA confirm BBBBBB", codes)
    assert not is_explicit_confirmation("全部 AAAAAA 确认 BBBBBB", codes)
    assert not is_explicit_confirmation("confirm AAAAAA all BBBBBB", codes)


def test_pending_row_without_confirmation_binding_cannot_be_approved(tmp_path):
    """Even if an unbound row is pending (e.g. edited by hand), None == None must not approve it."""
    store = Store(str(tmp_path / "s.db"))
    gate = ActionGate(store)
    req = gate.create(action_type="cancel_subscription", target_ref="sub_x", summary="Cancel X", plan_steps=["a"])
    store.conn.execute("UPDATE action_requests SET confirmation_code=NULL, confirmation_text=NULL,"
                       " confirmation_sha256=NULL WHERE id=?", (req["id"],))
    store.conn.commit()
    for msg in ("confirm", "Confirm", "确认"):
        with pytest.raises(Exception, match="no bound confirmation"):
            gate.confirm(req["id"], msg, shown_text_sha256=None)
        with pytest.raises(Exception, match="no bound confirmation"):
            gate.confirm_batch([req["id"]], msg, shown_text_sha256=None)
    assert gate.get(req["id"])["status"] == "pending_user"
    store.close()
