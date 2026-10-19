"""Confirmation gate state machine and negative tests (M0 acceptance: unconfirmed actions cannot execute)."""
import pytest

from core.action_gate import (ActionNotAllowedError, GateError, InvalidTransitionError, NotApprovedError,
                              NotExplicitConfirmationError, is_explicit_confirmation)
from core.evidence import EvidenceStore
from executors.base import GuardedExecutor


def make(gate, **kw):
    args = dict(action_type="cancel_subscription", target_ref="sub_hulu", summary="Cancel Hulu ($17.99/month) on the web",
                plan_steps=["Open hulu.com account page", "Click Cancel", "Decline retention offers", "Screenshot confirmation"],
                expected_saving_cents=1799, risk_notes="Access continues until 10/24")
    args.update(kw)
    return gate.create(**args)


def evidence_id(store):
    return EvidenceStore(store, root="/tmp/unused").add_email("m-confirm-1", "sub_hulu", "Your cancellation is confirmed")["id"]


# --- explicit confirmation -----------------------------------------------------------------------
@pytest.mark.parametrize("msg", ["Confirm", "yes, cancel Hulu", "确认取消 Hulu", "Approved - go ahead", "同意", "Confirm all 3"])
def test_explicit_confirmations(msg):
    assert is_explicit_confirmation(msg)


@pytest.mark.parametrize("msg", ["", "   ", None, "maybe", "I guess so", "whatever", "好像可以", "随便", "confirm?",
                                 "Yes?", "no", "don't cancel", "不要", "先不确认", "ok", "sounds interesting",
                                 "probably yes", "wait, confirm later", "也许吧"])
def test_vague_negative_or_silent_replies_are_not_confirmation(msg):
    assert not is_explicit_confirmation(msg)


# --- happy path ----------------------------------------------------------------------------------
def test_full_lifecycle_pending_to_executed(gate, store):
    req = make(gate)
    assert req["status"] == "pending_user"
    req = gate.confirm(req["id"], "Yes, confirm cancelling Hulu")
    assert req["status"] == "approved" and req["approved_by_message"] == "Yes, confirm cancelling Hulu"
    run = gate.begin_execution(req["id"], "browser")
    run = gate.finish_execution(run["id"], "success", [evidence_id(store)])
    req = gate.get(req["id"])
    assert req["status"] == "executed"
    assert [h["to"] for h in req["history"]] == ["pending_user", "approved", "executed"]
    assert run["outcome"] == "success" and run["finished_at"]


def test_failed_run_marks_request_failed(gate):
    req = gate.confirm(make(gate)["id"], "confirm")
    run = gate.begin_execution(req["id"], "browser")
    gate.finish_execution(run["id"], "failed", error_code="playbook_broken")
    assert gate.get(req["id"])["status"] == "failed"


def test_needs_user_keeps_run_open_and_resumes(gate, store):
    req = gate.confirm(make(gate)["id"], "confirm")
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
        gate.confirm(req["id"], "confirm")


def test_vague_reply_does_not_approve(gate):
    req = make(gate)
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm(req["id"], "maybe")
    assert gate.get(req["id"])["status"] == "pending_user"
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_confirming_after_expiry_fails_and_marks_expired(gate, clock):
    req = make(gate)
    clock.advance(hours=72, seconds=1)
    with pytest.raises(InvalidTransitionError):
        gate.confirm(req["id"], "confirm")
    assert gate.get(req["id"])["status"] == "expired"


def test_default_expiry_is_72_hours(gate, clock):
    req = make(gate)
    clock.advance(hours=71)
    assert gate.expire_due() == []
    clock.advance(hours=1)
    assert gate.expire_due() == [req["id"]]
    assert gate.expire_due() == []  # idempotent


def test_executed_request_cannot_run_twice(gate, store):
    req = gate.confirm(make(gate)["id"], "confirm")
    run = gate.begin_execution(req["id"], "browser")
    gate.finish_execution(run["id"], "success", [evidence_id(store)])
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_purchase_is_disabled(gate):
    with pytest.raises(ActionNotAllowedError):
        make(gate, action_type="purchase", summary="Buy TV")


def test_purchase_row_inserted_directly_still_cannot_execute(gate, store):
    req = make(gate)
    store.update("action_requests", req["id"], {"action_type": "purchase", "status": "approved",
                                                "approved_by_message": "confirm"})
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_approved_flag_without_user_message_is_not_enough(gate, store):
    req = make(gate)
    store.update("action_requests", req["id"], {"status": "approved"})
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_success_requires_evidence(gate):
    req = gate.confirm(make(gate)["id"], "confirm")
    run = gate.begin_execution(req["id"], "browser")
    with pytest.raises(GateError):
        gate.finish_execution(run["id"], "success", [])


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

    req = gate.confirm(make(gate)["id"], "confirm")
    run = Boom(gate).run(req["id"])
    assert run["outcome"] == "failed" and run["error_code"] == "RuntimeError"
    assert gate.get(req["id"])["status"] == "failed"


# --- pre-authorization revoke window (FR-1-9, Q4) --------------------------------------------------
def test_preauthorized_request_waits_for_revoke_window(gate, clock):
    from datetime import timedelta
    req = make(gate, execute_not_before=clock.now + timedelta(hours=2))
    gate.confirm(req["id"], "Confirm: auto-cancel Peacock trial before it ends")
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    clock.advance(hours=2)
    assert gate.begin_execution(req["id"], "browser")["executor"] == "browser"


def test_user_can_revoke_inside_window(gate, clock):
    from datetime import timedelta
    req = make(gate, execute_not_before=clock.now + timedelta(hours=2))
    gate.confirm(req["id"], "confirm")
    gate.cancel_by_user(req["id"], "user replied stop")
    clock.advance(hours=3)
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")


def test_cannot_revoke_after_execution_started(gate):
    req = gate.confirm(make(gate)["id"], "confirm")
    gate.begin_execution(req["id"], "browser")
    with pytest.raises(InvalidTransitionError):
        gate.cancel_by_user(req["id"])


# --- material change requires re-confirmation (8.2) -------------------------------------------------
def test_amend_after_approval_requires_reconfirmation(gate):
    req = gate.confirm(make(gate)["id"], "confirm")
    req = gate.amend(req["id"], {"plan_steps": ["Call Hulu support"], "risk_notes": "switching to phone"},
                     "web cancel failed; phone needed")
    assert req["status"] == "pending_user" and req["approved_by_message"] is None
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "bland_call")
    gate.confirm(req["id"], "yes, confirm the phone call")
    assert gate.begin_execution(req["id"], "bland_call")


def test_amend_rejects_status_changes(gate):
    req = make(gate)
    with pytest.raises(GateError):
        gate.amend(req["id"], {"status": "approved"}, "sneaky")


# --- batch -------------------------------------------------------------------------------------
def test_batch_confirm_all_or_nothing(gate):
    a, b, c = (make(gate, target_ref=f"sub_{i}") for i in range(3))
    gate.reject(c["id"])
    with pytest.raises(InvalidTransitionError):
        gate.confirm_batch([a["id"], b["id"], c["id"]], "Confirm all three")
    assert gate.get(a["id"])["status"] == "pending_user"
    approved = gate.confirm_batch([a["id"], b["id"]], "Confirm both")
    assert all(r["status"] == "approved" and "batch[" in r["approved_by_message"] for r in approved)


def test_batch_rejects_vague_reply(gate):
    a = make(gate)
    with pytest.raises(NotExplicitConfirmationError):
        gate.confirm_batch([a["id"]], "sure, whatever")


# --- creation validation -------------------------------------------------------------------------
def test_disclosed_fields_must_be_preapproved(gate):
    with pytest.raises(GateError):
        make(gate, disclosed_fields=[{"field": "date_of_birth", "value_masked": "**/**/1990"}])
    req = make(gate, disclosed_fields=[{"field": "account_last4", "value_masked": "****4821"}])
    assert req["disclosed_fields"][0]["field"] == "account_last4"


def test_disclosed_value_cannot_contain_full_card(gate):
    from core.privacy import ForbiddenDataError
    with pytest.raises(ForbiddenDataError):
        make(gate, disclosed_fields=[{"field": "account_last4", "value_masked": "4111 1111 1111 1111"}])


@pytest.mark.parametrize("atype", ["send_email", "place_call", "negotiate_bill"])
def test_email_and_calls_need_a_draft(gate, atype):
    with pytest.raises(GateError):
        make(gate, action_type=atype)
    assert make(gate, action_type=atype, draft_content="Hello, I am an AI assistant calling on behalf of Alex.")


def test_plan_steps_required(gate):
    with pytest.raises(GateError):
        make(gate, plan_steps=[])


def test_timeline_lists_requests_with_runs(gate, store):
    r1 = gate.confirm(make(gate)["id"], "confirm")
    run = gate.begin_execution(r1["id"], "browser")
    gate.finish_execution(run["id"], "success", [evidence_id(store)])
    make(gate, target_ref="sub_other")
    tl = gate.timeline()
    assert len(tl) == 2
    assert {t["request"]["id"]: (t["run"] or {}).get("outcome") for t in tl}[r1["id"]] == "success"
