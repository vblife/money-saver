"""M1b trials: reminders (FR-1-8), replies, pre-authorized cancel with a 2-hour undo (FR-1-9), R10 (10.2)."""
from datetime import date, datetime, timezone

import pytest

from core.action_gate import NotApprovedError
from core.classifier import Classifier
from core.gmail_scanner import ingest_messages
from core.merchant_resolver import MerchantResolver
from modules.subscriptions import SubscriptionInventory
from modules.trials import TrialError, Trials, is_stop_reply

ACCT = "alex@example.com"


def Z(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.fixture(scope="module")
def resolver():
    return MerchantResolver()


def add_trial(store, resolver, clock, mid="t1", merchant="Peacock", domain="peacocktv.com",
              body="Your 7-day free trial has started. After your trial ends on Oct 26, 2026 you'll be charged $7.99/month unless you cancel.",
              received="2026-10-19T16:00:00Z", frm=None):
    ingest_messages(store, ACCT, [{"id": mid, "from": frm or f"{merchant} <hello@{domain}>",
                                   "subject": f"Welcome to your {merchant} free trial", "body": body,
                                   "date": received}], Classifier(resolver))
    SubscriptionInventory(store, resolver).build(clock().astimezone(timezone.utc).date())
    subs = [s for s in store.find("subscriptions") if s["status"] == "trial"]
    return subs[-1]


def reminders(store, sub_id, status=None):
    rows = store.find("reminders", {"kind": "trial_end", "target_ref": sub_id}, order_by="fire_at")
    return [r for r in rows if status is None or r["status"] == status]


# --- scheduling ---------------------------------------------------------------------------------
def test_t2_t1_at_9am_local_and_idempotent(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    out = t.schedule_reminders()
    assert len(out["scheduled"]) == 2
    assert [r["fire_at"] for r in reminders(store, sub["id"])] == ["2026-10-24T16:00:00Z", "2026-10-25T16:00:00Z"]
    assert t.schedule_reminders() == {"scheduled": [], "dismissed": []}


def test_reminders_follow_dst_change(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock, body="Your free trial ends on Nov 3, 2026. Then $7.99/month.")
    Trials(store, gate, resolver).schedule_reminders()
    # DST ends Nov 1, 2026 in Los Angeles: 09:00 PST is 17:00 UTC
    assert [r["fire_at"] for r in reminders(store, sub["id"])] == ["2026-11-01T17:00:00Z", "2026-11-02T17:00:00Z"]


def test_user_timezone_is_used(store, gate, clock, resolver):
    store.update_user({"timezone": "America/New_York"})
    sub = add_trial(store, resolver, clock)
    Trials(store, gate, resolver).schedule_reminders()
    assert reminders(store, sub["id"])[0]["fire_at"] == "2026-10-24T13:00:00Z"


def test_changed_trial_end_reschedules(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    ingest_messages(store, ACCT, [{"id": "t1b", "from": "Peacock <hello@peacocktv.com>",
                                   "subject": "Your Peacock free trial ends soon",
                                   "body": "Your free trial ends on Oct 28, 2026. Then $7.99/month.",
                                   "date": "2026-10-20T16:00:00Z"}], Classifier(resolver))
    clock.advance(days=1)
    SubscriptionInventory(store, resolver).build(date(2026, 10, 20))
    out = t.schedule_reminders()
    assert len(out["dismissed"]) == 2 and len(out["scheduled"]) == 2
    assert [r["fire_at"] for r in reminders(store, sub["id"], "scheduled")] == ["2026-10-26T16:00:00Z",
                                                                               "2026-10-27T16:00:00Z"]


def test_late_scheduling_gives_one_overdue_reminder(store, gate, clock, resolver):
    clock.now = Z("2026-10-25T20:00:00Z")  # 13:00 local on T-1
    sub = add_trial(store, resolver, clock, received="2026-10-25T16:00:00Z")
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    assert [r["fire_at"] for r in reminders(store, sub["id"])] == ["2026-10-25T16:00:00Z"]
    msgs = t.dispatch_due()["messages"]
    assert len(msgs) == 1 and "(tomorrow)" in msgs[0]["text"]


# --- dispatch (R3) ------------------------------------------------------------------------------
def test_dispatch_sends_once_with_options(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    assert t.dispatch_due()["messages"] == []
    clock.now = Z("2026-10-24T16:00:00Z")
    (m,) = t.dispatch_due()["messages"]
    assert m["subscription_id"] == sub["id"] and m["options"] == ["cancel", "keep", "remind_later"]
    assert "ends on 2026-10-26 (in 2 days)" in m["text"] and "$7.99/month" in m["text"]
    assert "[Cancel] [Keep] [Remind me later]" in m["text"]
    assert t.dispatch_due()["messages"] == []
    clock.now = Z("2026-10-25T16:00:00Z")
    (m,) = t.dispatch_due()["messages"]
    assert "(tomorrow)" in m["text"]


def test_dispatch_holds_in_quiet_hours(store, gate, clock, resolver):
    add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    clock.now = Z("2026-10-25T06:30:00Z")          # 23:30 local: quiet
    out = t.dispatch_due()
    assert out["messages"] == [] and len(out["held_for_quiet_hours"]) == 1
    clock.now = Z("2026-10-25T15:30:00Z")          # 08:30 local
    assert len(t.dispatch_due()["messages"]) == 1


def test_zh_reminder(store, gate, clock, resolver):
    add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    clock.now = Z("2026-10-24T16:00:00Z")
    text = t.dispatch_due("zh")["messages"][0]["text"]
    assert "免费试用将于 2026-10-26" in text and "[取消] [保留] [稍后提醒]" in text


def test_stale_reminder_after_trial_end_is_dismissed(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    clock.now = Z("2026-10-27T17:00:00Z")
    out = t.dispatch_due()
    assert out["messages"] == [] and len(out["dismissed"]) == 2
    assert reminders(store, sub["id"], "scheduled") == []


# --- replies ------------------------------------------------------------------------------------
def test_keep_stops_reminders(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    out = t.respond(sub["id"], "keep")
    assert len(out["dismissed"]) == 2 and store.get("subscriptions", sub["id"])["user_decision"] == "keep"
    assert t.schedule_reminders()["scheduled"] == []
    clock.now = Z("2026-10-25T16:00:00Z")
    assert t.dispatch_due()["messages"] == []


def test_remind_later_next_9am_and_one_message_per_trial(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    clock.now = Z("2026-10-24T16:05:00Z")
    t.dispatch_due()
    out = t.respond(sub["id"], "remind_later")
    assert out["remind_at"] == "2026-10-25T16:00:00Z"
    clock.now = Z("2026-10-25T16:00:00Z")
    assert len(t.dispatch_due()["messages"]) == 1   # snoozed T-2 and T-1 land together: one message


def test_remind_later_is_capped_and_refused_when_too_late(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    clock.now = Z("2026-10-25T17:00:00Z")   # 10:00 local on T-1
    t.dispatch_due()
    assert t.respond(sub["id"], "remind_later")["remind_at"] == "2026-10-26T03:00:00Z"  # 20:00 local T-1
    clock.now = Z("2026-10-26T03:30:00Z")   # 20:30 local
    with pytest.raises(TrialError, match="too soon"):
        t.respond(sub["id"], "remind_later")


def test_cancel_reply_creates_unapproved_request(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    out = t.respond(sub["id"], "cancel")
    req = out["request"]
    assert req["status"] == "pending_user" and req["executor"] == "browser"
    assert req["target_ref"] == sub["id"] and "https://www.peacocktv.com/account" in req["plan_steps"][0]
    assert any("never ask for or store passwords" in s for s in req["plan_steps"])
    assert any("retention offer" in s for s in req["plan_steps"])
    assert "unverified" in req["risk_notes"]
    assert req["expected_saving_cents"] == 799
    assert Z(req["expires_at"]) <= Z("2026-10-26T07:00:00Z")   # not past the trial end (local midnight)
    assert "confirm " + req["confirmation_code"] in out["confirmation_text"]
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    with pytest.raises(TrialError, match="already an open"):
        t.respond(sub["id"], "cancel")
    assert store.get("subscriptions", sub["id"])["status"] == "trial"   # nothing cancelled


def test_cancel_plan_for_store_billed_and_phone(store, gate, clock, resolver):
    apple = add_trial(store, resolver, clock, mid="ap", merchant="Calm", domain="calm.com",
                      frm="Apple <no_reply@email.apple.com>",
                      body="Your Calm Premium free trial has started. Your trial ends on Oct 30, 2026, then $14.99/month.")
    assert apple["billing_channel"] == "apple"
    req = Trials(store, gate, resolver).respond(apple["id"], "cancel")["request"]
    assert req["executor"] == "manual" and "Apple ID > Subscriptions" in req["plan_steps"][0]
    sx = add_trial(store, resolver, clock, mid="sx", merchant="SiriusXM", domain="siriusxm.com",
                   body="Your SiriusXM free trial has started. Your trial ends on Nov 5, 2026, then $9.99/month.")
    req = Trials(store, gate, resolver).respond(sx["id"], "cancel")["request"]
    assert req["executor"] == "manual" and "by phone" in req["plan_steps"][0]


@pytest.mark.parametrize("choice", ["", "delete", "CANCEL"])
def test_bad_choice(store, gate, clock, resolver, choice):
    sub = add_trial(store, resolver, clock)
    with pytest.raises(TrialError):
        Trials(store, gate, resolver).respond(sub["id"], choice)


def test_reply_on_non_trial_is_refused(store, gate, clock, resolver):
    from modules.subscriptions import add_manual_subscription
    s = add_manual_subscription(store, "Gym", 4000, "monthly")
    with pytest.raises(TrialError, match="not a free trial"):
        Trials(store, gate, resolver).respond(s["id"], "cancel")


# --- trial length inference (FR-1-7) ----------------------------------------------------------------
def test_stated_length_and_default_length(store, gate, clock, resolver):
    stated = add_trial(store, resolver, clock, mid="len", body="Your 14-day free trial has started. Then $7.99/month.")
    assert stated["trial_end_date"] == "2026-11-02" and stated["confidence"] > 0.45
    guessed = add_trial(store, resolver, clock, mid="gs", merchant="Hulu", domain="hulu.com",
                        body="Thanks for starting your free trial. Then $17.99/month.")
    assert guessed["trial_end_date"] == "2026-10-26" and guessed["confidence"] <= 0.45
    t = Trials(store, gate, resolver)
    t.schedule_reminders()
    clock.now = Z("2026-10-24T16:00:00Z")
    texts = {m["subscription_id"]: m["text"] for m in t.dispatch_due()["messages"]}
    assert "is a guess" in texts[guessed["id"]]
    with pytest.raises(TrialError, match="only a guess"):
        t.preauthorize(guessed["id"])
    assert t.preauthorize(stated["id"])["request"]["status"] == "pending_user"


# --- pre-authorization + undo (FR-1-9) ---------------------------------------------------------------
def _preauth_approved(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    out = t.preauthorize(sub["id"])
    req = out["request"]
    assert req["execute_not_before"] == "2026-10-25T18:00:00Z"   # T-1 11:00 local
    assert req["constraints"]["preauthorized"] is True and "(pre-authorized)" in req["summary"]
    assert "reply stop to undo" in req["risk_notes"]
    gate.confirm(req["id"], f"confirm {req['confirmation_code']}")
    return sub, t, req


def test_preauth_warning_then_ready_after_window(store, gate, clock, resolver):
    sub, t, req = _preauth_approved(store, gate, clock, resolver)
    out = t.trial_guard()
    assert store.get("subscriptions", sub["id"])["auto_cancel_before_trial"] == 1
    assert out["warnings"] == [] and out["ready_to_execute"] == []
    assert reminders(store, sub["id"], "scheduled") == []   # approved: no T-2/T-1 nags
    clock.now = Z("2026-10-25T16:00:00Z")                   # T-1 09:00 local
    (w,) = t.trial_guard()["warnings"]
    assert "2026-10-25 11:00" in w["text"] and "Reply stop" in w["text"]
    assert t.trial_guard()["warnings"] == []                # once
    clock.now = Z("2026-10-25T17:59:00Z")
    assert t.trial_guard()["ready_to_execute"] == []
    with pytest.raises(NotApprovedError, match="revoke window"):
        gate.begin_execution(req["id"], "browser")
    clock.now = Z("2026-10-25T18:00:00Z")
    assert t.trial_guard()["ready_to_execute"] == [req["id"]]
    assert store.get("action_requests", req["id"])["status"] == "approved"   # listed, not executed
    assert store.count("action_runs") == 0


def test_late_warning_delays_readiness(store, gate, clock, resolver):
    sub, t, req = _preauth_approved(store, gate, clock, resolver)
    clock.now = Z("2026-10-25T18:30:00Z")   # guard missed 09:00
    out = t.trial_guard()
    assert len(out["warnings"]) == 1 and out["ready_to_execute"] == []
    assert "2026-10-25 13:30" in out["warnings"][0]["text"]
    clock.now = Z("2026-10-25T20:29:00Z")
    assert t.trial_guard()["ready_to_execute"] == []
    clock.now = Z("2026-10-25T20:30:00Z")
    assert t.trial_guard()["ready_to_execute"] == [req["id"]]


@pytest.mark.parametrize("reply", ["stop", "STOP!", "Undo", "please don't cancel", "停止", "撤销", "别取消"])
def test_stop_reply_undoes(store, gate, clock, resolver, reply):
    sub, t, req = _preauth_approved(store, gate, clock, resolver)
    clock.now = Z("2026-10-25T16:00:00Z")
    t.trial_guard()
    out = t.revoke(sub["id"], reply)
    assert out["revoked_request_ids"] == [req["id"]]
    assert store.get("action_requests", req["id"])["status"] == "cancelled_by_user"
    clock.now = Z("2026-10-25T18:00:00Z")
    assert t.trial_guard()["ready_to_execute"] == []
    with pytest.raises(NotApprovedError):
        gate.begin_execution(req["id"], "browser")
    assert store.get("subscriptions", sub["id"])["auto_cancel_before_trial"] == 0


@pytest.mark.parametrize("reply", ["ok", "sounds good", "confirm", "", "取消"])
def test_non_stop_reply_changes_nothing(store, gate, clock, resolver, reply):
    sub, t, req = _preauth_approved(store, gate, clock, resolver)
    with pytest.raises(TrialError):
        t.revoke(sub["id"], reply)
    assert store.get("action_requests", req["id"])["status"] == "approved"


def test_cannot_undo_after_execution_started(store, gate, clock, resolver):
    sub, t, req = _preauth_approved(store, gate, clock, resolver)
    clock.now = Z("2026-10-25T16:00:00Z")
    t.trial_guard()
    clock.now = Z("2026-10-25T18:00:00Z")
    gate.begin_execution(req["id"], "browser")
    with pytest.raises(Exception, match="already started"):
        t.revoke(sub["id"], "stop")


def test_preauth_refusals(store, gate, clock, resolver):
    t = Trials(store, gate, resolver)
    apple = add_trial(store, resolver, clock, mid="ap", merchant="Calm", domain="calm.com",
                      frm="Apple <no_reply@email.apple.com>",
                      body="Your Calm Premium free trial has started. Your trial ends on Oct 30, 2026, then $14.99/month.")
    with pytest.raises(TrialError, match="browser path"):
        t.preauthorize(apple["id"])
    clock.now = Z("2026-10-26T06:00:00Z")   # 23:00 local on T-1
    late = add_trial(store, resolver, clock, mid="late", received="2026-10-25T16:00:00Z")
    with pytest.raises(TrialError, match="too close"):
        t.preauthorize(late["id"])


def test_preauth_is_only_a_request_until_confirmed(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock)
    t = Trials(store, gate, resolver)
    req = t.preauthorize(sub["id"])["request"]
    clock.now = Z("2026-10-25T16:00:00Z")
    out = t.trial_guard()
    assert out["warnings"] == [] and store.get("subscriptions", sub["id"])["auto_cancel_before_trial"] == 0
    with pytest.raises(Exception):
        gate.confirm(req["id"], "yes please")


# --- R10 ---------------------------------------------------------------------------------------------
def test_expired_trial_request_gets_one_last_reminder(store, gate, clock, resolver):
    sub = add_trial(store, resolver, clock, body="Your free trial ends on Nov 10, 2026. Then $7.99/month.")
    t = Trials(store, gate, resolver)
    req = t.respond(sub["id"], "cancel")["request"]
    clock.advance(hours=73)
    out = t.expiry()
    assert out["expired"] == [req["id"]] and len(out["messages"]) == 1
    assert "expired without confirmation" in out["messages"][0]["text"]
    assert t.expiry() == {"expired": [], "messages": []}


@pytest.mark.parametrize("msg,expected", [("stop", True), ("Stop it", True), ("停止", True), ("keep it", True),
                                          ("go ahead", False), ("", False), (None, False)])
def test_is_stop_reply(msg, expected):
    assert is_stop_reply(msg) is expected
