"""M1d-3: after a cancellation run (FR-1-13 confirmation watch, FR-1-15 post-charge check, 6.11 ledger rules)."""
import pytest

from core.evidence import EvidenceStore
from core.ledger import Ledger
from modules.after_cancel import CancelFollowUp, FollowUpError
from modules.subscriptions import add_manual_subscription


@pytest.fixture
def ev(store):
    return EvidenceStore(store, str(store.path) + ".ev")


def sub(store, amount=1799, period="monthly", next_charge="2026-11-03"):
    return add_manual_subscription(store, "Netflix", amount, period, next_charge_date=next_charge,
                                   merchant_id="netflix", billing_channel="direct")


def finished_run(gate, store, ev, s, kinds=("screenshot",), outcome="success"):
    req = gate.create("cancel_subscription", s["id"], "Cancel Netflix", ["Cancel"], executor="manual")
    gate.confirm(req["id"], f"confirm {req['confirmation_code']}")
    run = gate.begin_execution(req["id"], "manual")
    ids = []
    for k in kinds:
        if k == "confirmation_email":
            ids.append(ev.add_email("msg-confirm-1", s["id"], "Your membership has been cancelled")["id"])
        else:
            ids.append(ev.add_bytes(k, b"fake", "x.png", s["id"])["id"])
    return gate.finish_execution(run["id"], outcome, ids or None, None if outcome == "success" else "x")


def test_success_without_email_starts_the_watch_and_a_pending_entry(store, gate, ev, clock):
    s = sub(store)
    run = finished_run(gate, store, ev, s)
    out = CancelFollowUp(store, gate).on_run_finished(run)
    assert out["subscription_status"] == "cancel_in_progress"
    w = out["watch"]
    assert (w["status"], w["cancelled_on"], w["charge_check_on"]) == ("awaiting_confirmation", "2026-10-19",
                                                                      "2026-11-06")
    e = out["ledger_entry"]
    assert (e["status"], e["dedupe_key"], e["amount_cents"], e["saving_type"]) == ("pending", f"cancel:{s['id']}",
                                                                                  1799, "recurring")
    assert e["annualized_cents"] == 1799 * 12 and e["action_run_id"] == run["id"]
    again = CancelFollowUp(store, gate).on_run_finished(run)
    assert again["already_recorded"] and len(store.find("savings_ledger")) == 1


def test_success_with_confirmation_email_is_cancelled_and_verified(store, gate, ev):
    s = sub(store)
    out = CancelFollowUp(store, gate).on_run_finished(finished_run(gate, store, ev, s,
                                                                   ("screenshot", "confirmation_email")))
    assert out["subscription_status"] == "cancelled" and store.get("subscriptions", s["id"])["status"] == "cancelled"
    assert out["ledger_entry"]["status"] == "verified" and out["watch"]["status"] == "confirmed"


def test_annual_subscription_uses_monthly_equivalent(store, gate, ev):
    s = sub(store, amount=12000, period="annual")
    out = CancelFollowUp(store, gate).on_run_finished(finished_run(gate, store, ev, s))
    assert out["ledger_entry"]["amount_cents"] == store.get("subscriptions", s["id"])["monthly_equiv_cents"]


def test_failed_or_other_runs_are_ignored(store, gate, ev):
    s = sub(store)
    assert CancelFollowUp(store, gate).on_run_finished(finished_run(gate, store, ev, s, (), outcome="failed")) is None
    assert store.find("cancel_watches") == [] and store.find("savings_ledger") == []


def test_unconfirmed_after_48_hours_then_confirmed_by_email(store, gate, ev, clock):
    s = sub(store)
    fu = CancelFollowUp(store, gate)
    fu.on_run_finished(finished_run(gate, store, ev, s))
    clock.advance(hours=47)
    assert fu.check()["notices"] == []
    clock.advance(hours=2)
    [n] = fu.check()["notices"]
    assert n["kind"] == "unconfirmed" and "48 hours" in n["message"]
    assert store.get("subscriptions", s["id"])["status"] == "cancel_unconfirmed"
    assert fu.check()["notices"] == []  # reported once
    shot = ev.add_bytes("screenshot", b"x", "a.png", s["id"])
    with pytest.raises(FollowUpError, match="confirmation email"):
        fu.confirm(s["id"], shot["id"])
    mail = ev.add_email("msg-late", s["id"])
    out = fu.confirm(s["id"], mail["id"])
    assert out["subscription_status"] == "cancelled" and out["ledger_entry"]["status"] == "verified"
    with pytest.raises(FollowUpError):
        fu.confirm(s["id"], mail["id"])


def test_charge_after_cancel_reverses_and_proposes_a_refund(store, gate, ev, clock):
    s = sub(store)
    fu = CancelFollowUp(store, gate)
    fu.on_run_finished(finished_run(gate, store, ev, s))
    store.insert("charges", {"subscription_id": s["id"], "date": "2026-10-19", "amount_cents": 1799,
                             "source": "card_statement", "source_ref": "line-0"})  # the day of cancelling
    store.insert("charges", {"subscription_id": s["id"], "date": "2026-11-03", "amount_cents": 1799,
                             "source": "card_statement", "source_ref": "line-1"})
    clock.advance(days=17)  # 2026-11-05: not yet due
    assert [n["kind"] for n in fu.check()["notices"]] == ["unconfirmed"]
    clock.advance(days=1)  # 2026-11-06
    [n] = fu.check("zh")["notices"]
    assert n["kind"] == "charged_after_cancel" and "退款" in n["message"]
    assert store.get("subscriptions", s["id"])["status"] == "charged_after_cancel"
    req = gate.get(n["refund_request_id"])
    assert (req["action_type"], req["executor"], req["status"]) == ("submit_claim", "manual", "pending_user")
    assert req["expected_saving_cents"] == 1799 and req["constraints"]["reason"] == "charged_after_cancel"
    assert store.find("action_runs", {"action_request_id": req["id"]}) == []  # nothing runs until confirmed
    assert Ledger(store).month_summary(2026, 11)["pending_cents"] == 0  # the entry is reversed
    assert fu.check()["notices"] == []


def test_no_charge_is_not_proof(store, gate, ev, clock):
    s = sub(store)
    fu = CancelFollowUp(store, gate)
    fu.on_run_finished(finished_run(gate, store, ev, s))
    clock.advance(days=18)
    kinds = [n["kind"] for n in fu.check()["notices"]]
    assert kinds == ["unconfirmed", "no_charge_seen"]
    [e] = store.find("savings_ledger")
    assert e["status"] == "pending"
    assert fu.check()["notices"] == []


def test_server_hooks_finish_action_run(tmp_path):
    from server.server import TOOLS, App
    assert {"cancel_watch_status", "confirm_cancellation", "check_cancel_watches"} <= {t["name"] for t in TOOLS}
    app = App(str(tmp_path / "msb.db"), str(tmp_path / "ev"), [str(tmp_path / "inbox")])
    s = sub(app.store)
    made = app.call("create_action_request", {"action_type": "cancel_subscription", "target_ref": s["id"],
                                              "summary": "Cancel Netflix", "plan_steps": ["Cancel"],
                                              "executor": "manual"})
    rid = made["request"]["id"]
    app.call("confirm_action_request", {"request_id": rid, "user_message": f"confirm {made['confirmation_code']}"})
    run = app.call("start_action_run", {"request_id": rid, "executor": "manual"})["run"]
    mail = app.evidence.add_email("msg-1", s["id"])
    out = app.call("finish_action_run", {"run_id": run["id"], "outcome": "success", "evidence_ids": [mail["id"]]})
    assert out["follow_up"]["subscription_status"] == "cancelled"
    assert app.call("cancel_watch_status", {"subscription_id": s["id"]})["watches"][0]["status"] == "confirmed"
    assert app.call("check_cancel_watches", {})["notices"] == []
