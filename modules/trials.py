"""Module 1b: free-trial reminders, replies, and pre-authorized cancellation (FR-1-7, FR-1-8, FR-1-9; US-1-4).

* Reminders: ``trial_end`` rows at T-2 and T-1, 09:00 in the user's time zone (FR-1-8). Re-running gives the same
  rows. If the trial end date changes, the old rows are dismissed. If the user decides to keep the trial, or a
  cancellation is approved, the reminders stop.
* Replies: [Cancel] creates a ``cancel_subscription`` action request that still needs the user's explicit
  confirmation. [Keep] stops the reminders. [Remind me later] snoozes until the next 09:00, but never past 20:00 the
  day before the trial ends.
* Pre-authorization (FR-1-9, Q4 default): the user confirms one specific request ahead of time through the normal
  gate. Its ``execute_not_before`` is T-1 11:00 local. At T-1 09:00 the bot posts "cancelling in 2 hours, reply stop
  to undo". A run can only start once that warning has been out for 2 hours. "Stop" revokes the request (the undo).
  Pre-authorization only covers the browser path: phone and email cancellations always get a real-time
  confirmation, because the call script (Q3) and the exact email have to be approved as they are.

Nothing here executes a cancellation. ``trial_guard`` reports which approved requests are ready, and execution is
M1d (behind the gate, stubbed in tests). Reminder texts are returned to the bot to post in the user's own chat.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from core import notifier
from core.action_gate import ActionGate, GateError
from core.merchant_resolver import MerchantResolver
from core.store import Store
from core.util import parse_iso, to_iso
from modules.subscriptions import _tz, trial_basis

REMINDER_OFFSETS_DAYS = (2, 1)          # FR-1-8
REMINDER_HOUR = 9                        # 09:00 local
SNOOZE_CAP = time(20, 0)                 # remind-later never goes past 20:00 on T-1
PREAUTH_EXEC_TIME = time(11, 0)          # T-1 09:00 warning + 2-hour revoke window
REVOKE_WINDOW = timedelta(hours=2)
PREAUTH_EXECUTORS = ("browser",)
CHOICES = ("cancel", "keep", "remind_later")
# Q11 is Charles's decision. Until then, every merchant whose registry entry is not verified gets an explicit
# warning on its cancel request. TODO(Q11): replace with whatever review rule Charles picks.
WARN_UNVERIFIED_MERCHANTS = True
STOP_WORDS = ("stop", "undo", "don't cancel", "do not cancel", "keep it", "停止", "撤销", "不要取消", "别取消", "保留")


class TrialError(GateError):
    """A trial operation that is not allowed or not possible right now."""


def local_at(d: date, at: time, tz) -> datetime:
    return datetime.combine(d, at, tzinfo=tz).astimezone(timezone.utc)


def is_stop_reply(message: str | None) -> bool:
    """Undo is deliberately lenient: any clear stop/undo wording revokes. Revoking is the safe direction."""
    text = (message or "").strip().lower()
    return bool(text) and any(w in text for w in STOP_WORDS)


def _quiet(now_local: datetime, prefs: dict[str, Any]) -> bool:
    start, end = (prefs.get("quiet_hours") or ["22:00", "08:00"])[:2]
    t = now_local.time()
    s, e = time.fromisoformat(start), time.fromisoformat(end)
    return (s <= t or t < e) if s > e else (s <= t < e)


class Trials:
    def __init__(self, store: Store, gate: ActionGate, resolver: MerchantResolver | None = None):
        self.store, self.gate = store, gate
        self.resolver = resolver or MerchantResolver()

    # -- helpers ---------------------------------------------------------------------------
    @property
    def tz(self):
        return _tz(self.store.get_user().get("timezone"))

    def now(self) -> datetime:
        return self.gate.now_fn()

    def _sub(self, sub_id: str) -> dict[str, Any]:
        sub = self.store.get("subscriptions", sub_id)
        if sub is None:
            raise KeyError(f"unknown subscription {sub_id}")
        return sub

    def _cancel_requests(self, sub_id: str, statuses=("pending_user", "approved")) -> list[dict[str, Any]]:
        return [r for r in self.store.find("action_requests", {"action_type": "cancel_subscription",
                                                                 "target_ref": sub_id}) if r["status"] in statuses]

    @staticmethod
    def _is_preauth(req: dict[str, Any]) -> bool:
        return bool((req.get("constraints") or {}).get("preauthorized")) and bool(req.get("execute_not_before"))

    def _reminders(self, target: str, kind: str = "trial_end") -> list[dict[str, Any]]:
        return self.store.find("reminders", {"kind": kind, "target_ref": target}, order_by="fire_at")

    def _dismiss(self, target: str, kind: str = "trial_end") -> list[str]:
        out = []
        for r in self._reminders(target, kind):
            if r["status"] in ("scheduled", "snoozed"):
                self.store.update("reminders", r["id"], {"status": "dismissed"})
                out.append(r["id"])
        return out

    def _wants_reminders(self, sub: dict[str, Any]) -> bool:
        if sub["status"] != "trial" or not sub.get("trial_end_date") or sub.get("user_decision") == "keep":
            return False
        return not self._cancel_requests(sub["id"], ("approved", "executed"))

    # -- FR-1-8 scheduling -----------------------------------------------------------------
    def schedule_reminders(self) -> dict[str, list[str]]:
        now, tz = self.now(), self.tz
        scheduled, dismissed = [], []
        for sub in self.store.find("subscriptions"):
            existing = self._reminders(sub["id"])
            if not self._wants_reminders(sub):
                dismissed += self._dismiss(sub["id"])
                continue
            end = date.fromisoformat(sub["trial_end_date"])
            times = [local_at(end - timedelta(days=k), time(REMINDER_HOUR), tz) for k in REMINDER_OFFSETS_DAYS]
            wanted = [t for t in times if t >= now]
            if not wanted and now < local_at(end, time(0), tz):
                wanted = [times[-1]]  # both slots passed but the trial has not ended: one overdue T-1 reminder
            wanted_iso = {to_iso(t) for t in wanted}
            for r in existing:
                if r["fire_at"] not in wanted_iso and r["status"] in ("scheduled", "snoozed"):
                    self.store.update("reminders", r["id"], {"status": "dismissed"})
                    dismissed.append(r["id"])
            have = {r["fire_at"] for r in existing}
            for iso in sorted(wanted_iso - have):
                scheduled.append(self.store.insert("reminders", {"kind": "trial_end", "target_ref": sub["id"],
                                                                 "fire_at": iso})["id"])
        return {"scheduled": scheduled, "dismissed": dismissed}

    # -- R3 dispatch -----------------------------------------------------------------------
    def dispatch_due(self, lang: str = "en") -> dict[str, Any]:
        """R3: due trial reminders -> texts for the bot to post to the user. Holds them in quiet hours."""
        now, tz = self.now(), self.tz
        prefs = self.store.get_user().get("notification_prefs") or {}
        today = now.astimezone(tz).date()
        messages, held, dismissed = [], [], []
        for r in self.store.find("reminders", {"kind": "trial_end"}, order_by="fire_at"):
            due = (r["status"] == "scheduled" and parse_iso(r["fire_at"]) <= now) or (
                r["status"] == "snoozed" and r.get("snooze_until") and parse_iso(r["snooze_until"]) <= now)
            if not due:
                continue
            sub = self.store.get("subscriptions", r["target_ref"])
            if sub is None or not self._wants_reminders(sub) or date.fromisoformat(sub["trial_end_date"]) < today:
                self.store.update("reminders", r["id"], {"status": "dismissed"})
                dismissed.append(r["id"])
                continue
            if _quiet(now.astimezone(tz), prefs):
                held.append(r["id"])
                continue
            if sub["id"] in {m["subscription_id"] for m in messages}:
                self.store.update("reminders", r["id"], {"status": "sent", "snooze_until": None})
                continue  # one reminder per trial per run (a snoozed T-2 can land on the T-1 slot)
            days_left = (date.fromisoformat(sub["trial_end_date"]) - today).days
            pending = bool(self._cancel_requests(sub["id"], ("pending_user",)))
            text = notifier.format_trial_reminder(sub, days_left, trial_basis(self.store, sub), pending, lang)
            self.store.update("reminders", r["id"], {"status": "sent", "snooze_until": None})
            messages.append({"reminder_id": r["id"], "subscription_id": sub["id"], "text": text,
                             "options": list(CHOICES)})
        return {"messages": messages, "held_for_quiet_hours": held, "dismissed": dismissed}

    # -- replies ---------------------------------------------------------------------------
    def respond(self, sub_id: str, choice: str, lang: str = "en") -> dict[str, Any]:
        if choice not in CHOICES:
            raise TrialError(f"choice must be one of {CHOICES}")
        sub = self._sub(sub_id)
        if sub["status"] != "trial":
            raise TrialError(f"{sub['display_name']} is not a free trial (status {sub['status']})")
        if choice == "keep":
            self.store.update("subscriptions", sub_id, {"user_decision": "keep"})
            return {"subscription_id": sub_id, "decision": "keep", "dismissed": self._dismiss(sub_id)}
        if choice == "remind_later":
            return self._snooze(sub)
        req = self.create_cancel_request(sub_id, lang=lang)
        self.store.update("subscriptions", sub_id, {"user_decision": "cancel"})
        return {"subscription_id": sub_id, "decision": "cancel", "request": req,
                "confirmation_text": req["confirmation_text"],
                "note": "Nothing is cancelled yet: show confirmation_text and wait for the user's explicit confirmation."}

    def _snooze(self, sub: dict[str, Any]) -> dict[str, Any]:
        now, tz = self.now(), self.tz
        end = date.fromisoformat(sub["trial_end_date"])
        local = now.astimezone(tz)
        nxt = local_at(local.date() + timedelta(days=1 if local.time() >= time(REMINDER_HOUR) else 0),
                       time(REMINDER_HOUR), tz)
        cap = local_at(end - timedelta(days=1), SNOOZE_CAP, tz)
        until = min(nxt, cap)
        if until <= now:
            raise TrialError("the trial ends too soon to remind you later; please choose cancel or keep now")
        rows = [r for r in self._reminders(sub["id"]) if r["status"] in ("sent", "scheduled", "snoozed")]
        if rows:
            target = rows[-1]
            self.store.update("reminders", target["id"], {"status": "snoozed", "snooze_until": to_iso(until)})
            rid = target["id"]
        else:
            rid = self.store.insert("reminders", {"kind": "trial_end", "target_ref": sub["id"],
                                                  "fire_at": to_iso(until)})["id"]
        self.store.update("subscriptions", sub["id"], {"user_decision": "remind_later"})
        return {"subscription_id": sub["id"], "decision": "remind_later", "reminder_id": rid,
                "remind_at": to_iso(until)}

    # -- cancel requests -------------------------------------------------------------------
    def cancel_plan(self, sub: dict[str, Any]) -> tuple[str, list[str], list[str]]:
        """(executor, plan steps, risk notes) from the registry. Only describes; never executes."""
        m = self.resolver.get(sub.get("merchant_id") or "") or {}
        name = sub["display_name"]
        notes: list[str] = []
        channel = sub.get("billing_channel")
        if channel in ("apple", "google_play"):
            where = "Settings > Apple ID > Subscriptions" if channel == "apple" else "Google Play > Subscriptions"
            url = (self.resolver.get(channel) or {}).get("cancel_url")
            steps = [f"Send you the exact steps to cancel {name} yourself in {where}" + (f" ({url})" if url else ""),
                     "Watch your email for the cancellation confirmation and link it as evidence"]
            return "manual", steps, ["Store-billed subscriptions can only be cancelled from your own account."]
        method = m.get("cancel_method") or "web"
        if method == "web" and m.get("cancel_url"):
            steps = [f"Open {m['cancel_url']} in my browser",
                     "If a login is needed, ask you to log in yourself (I never ask for or store passwords)",
                     f"Follow the steps to cancel {name}; if a retention offer appears, do not accept it and ask you",
                     "Save a full-page screenshot and the confirmation number as evidence"]
            executor = "browser"
        elif method in ("phone", "email"):
            # Bot-placed calls and bot-sent emails come in M1d: a call needs an approved script (and the Q3
            # AI-disclosure decision), and an email needs the exact draft approved. Until then this is manual.
            contact = m.get("support_phone") if method == "phone" else None
            steps = [f"Send you the exact steps to cancel {name} by {method}"
                     + (f" (registry phone {contact})" if contact else "") + ", with what to say or write",
                     "Link the confirmation email as evidence when it arrives"]
            executor = "manual"
            notes.append(f"{name} cancels by {method}. In this version I prepare the steps and you contact them; "
                         "a bot-placed call or email would need its own confirmation of the exact script or email.")
        else:
            steps = [f"Send you the exact manual steps and link to cancel {name}"]
            executor = "manual"
        if WARN_UNVERIFIED_MERCHANTS and m.get("verification") != "verified":
            notes.append(f"The registry details for {m.get('name', name)} are unverified; I will check the official "
                         "site before acting.")
        return executor, steps, notes

    def create_cancel_request(self, sub_id: str, preauthorized: bool = False, lang: str = "en") -> dict[str, Any]:
        sub = self._sub(sub_id)
        executor, steps, notes = self.cancel_plan(sub)
        now, tz = self.now(), self.tz
        saving = sub.get("trial_converts_to_cents") or sub.get("amount_cents")
        kwargs: dict[str, Any] = {}
        summary = f"Cancel {sub['display_name']}"
        constraints: dict[str, Any] = {"subscription_id": sub_id}
        if sub.get("trial_end_date"):
            end = date.fromisoformat(sub["trial_end_date"])
            summary += f" before the free trial ends on {end.isoformat()}"
            constraints["trial_end_date"] = end.isoformat()
            deadline = local_at(end, time(0), tz)
            if deadline > now:
                kwargs["expires_at"] = min(deadline, now + timedelta(hours=72))
        if preauthorized:
            if executor not in PREAUTH_EXECUTORS:
                raise TrialError(f"pre-authorized cancellation is only available for the browser path; {sub['display_name']} "
                                 f"is cancelled by {executor}, so it needs your confirmation at the time")
            if not sub.get("trial_end_date"):
                raise TrialError("the trial end date is unknown, so an automatic cancellation cannot be scheduled")
            if trial_basis(self.store, sub) == "default_length":
                raise TrialError("the trial end date is only a guess (no date or length in the email); please check it in "
                                 "your account before scheduling an automatic cancellation")
            end = date.fromisoformat(sub["trial_end_date"])
            nb = local_at(end - timedelta(days=1), PREAUTH_EXEC_TIME, tz)
            if nb < now + REVOKE_WINDOW:
                nb = now + REVOKE_WINDOW
            if nb >= local_at(end, time(0), tz):
                raise TrialError("too close to the trial end to leave the 2-hour undo window; cancel now instead")
            kwargs["execute_not_before"] = nb
            constraints["preauthorized"] = True
            summary += " (pre-authorized)"
            notes.append(f"I will message you 2 hours before cancelling; reply stop to undo. Not before "
                         f"{nb.astimezone(tz).strftime('%Y-%m-%d %H:%M')} your time.")
        if self._cancel_requests(sub_id):
            raise TrialError("there is already an open cancellation request for this subscription")
        return self.gate.create(action_type="cancel_subscription", target_ref=sub_id, summary=summary,
                                plan_steps=steps, executor=executor, constraints=constraints,
                                expected_saving_cents=saving, risk_notes=" ".join(notes), lang=lang, **kwargs)

    def preauthorize(self, sub_id: str, lang: str = "en") -> dict[str, Any]:
        sub = self._sub(sub_id)
        if sub["status"] != "trial":
            raise TrialError(f"{sub['display_name']} is not a free trial")
        req = self.create_cancel_request(sub_id, preauthorized=True, lang=lang)
        return {"request": req, "confirmation_text": req["confirmation_text"],
                "note": "This is only a request. Show confirmation_text; it becomes active only after explicit confirmation."}

    def revoke(self, sub_id: str, user_message: str | None = None, note: str = "user said stop") -> dict[str, Any]:
        """The undo (FR-1-9 'reply stop'). Revokes pending or approved cancel requests that have not started."""
        if user_message is not None and not is_stop_reply(user_message):
            raise TrialError("that reply does not ask to stop; nothing was changed")
        self._sub(sub_id)
        revoked = []
        for req in self._cancel_requests(sub_id):
            self.gate.cancel_by_user(req["id"], note)
            revoked.append(req["id"])
            self._dismiss(req["id"], "follow_up")
        self.store.update("subscriptions", sub_id, {"auto_cancel_before_trial": False, "user_decision": "none"})
        return {"subscription_id": sub_id, "revoked_request_ids": revoked}

    # -- R4 trial guard ------------------------------------------------------------------
    def trial_guard(self, lang: str = "en") -> dict[str, Any]:
        now, tz = self.now(), self.tz
        sched = self.schedule_reminders()
        warnings, ready = [], []
        approved = {r["target_ref"]: r for r in self.store.find("action_requests", {"status": "approved",
                                                                                     "action_type": "cancel_subscription"})
                    if self._is_preauth(r)}
        for sub in self.store.find("subscriptions"):
            flag = sub["id"] in approved
            if bool(sub.get("auto_cancel_before_trial")) != flag:
                self.store.update("subscriptions", sub["id"], {"auto_cancel_before_trial": flag})
        for sub_id, req in sorted(approved.items()):
            nb = parse_iso(req["execute_not_before"])
            warn_at = nb - REVOKE_WINDOW
            sent = [r for r in self._reminders(req["id"], "follow_up") if r["status"] == "sent"]
            if not sent and now >= warn_at:
                self.store.insert("reminders", {"kind": "follow_up", "target_ref": req["id"],
                                                "fire_at": to_iso(now), "status": "sent"})
                sub = self.store.get("subscriptions", sub_id)
                warnings.append({"request_id": req["id"], "subscription_id": sub_id,
                                 "text": notifier.format_preauth_warning(sub, max(nb, now + REVOKE_WINDOW), tz, lang)})
                continue
            if sent and now >= nb and now >= parse_iso(sent[0]["fire_at"]) + REVOKE_WINDOW:
                try:
                    self.gate.require_approved(req["id"], req["executor"])
                except GateError:
                    continue
                ready.append(req["id"])
        return {**sched, "warnings": warnings, "ready_to_execute": ready,
                "note": "ready_to_execute is a list only; execution happens through start_action_run (M1d)."}

    # -- R10 -------------------------------------------------------------------------------
    def expiry(self, lang: str = "en") -> dict[str, Any]:
        expired = self.gate.expire_due()
        now, tz = self.now(), self.tz
        messages = []
        for rid in expired:
            req = self.store.get("action_requests", rid)
            if req["action_type"] != "cancel_subscription":
                continue
            sub = self.store.get("subscriptions", req["target_ref"])
            if not sub or sub["status"] != "trial" or not sub.get("trial_end_date"):
                continue
            if local_at(date.fromisoformat(sub["trial_end_date"]), time(0), tz) <= now:
                continue
            if self._reminders(rid, "follow_up"):
                continue
            self.store.insert("reminders", {"kind": "follow_up", "target_ref": rid, "fire_at": to_iso(now),
                                            "status": "sent"})
            messages.append({"request_id": rid, "subscription_id": sub["id"],
                             "text": notifier.format_expired_trial_request(sub, lang)})
        return {"expired": expired, "messages": messages}
