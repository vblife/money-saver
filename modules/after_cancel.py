"""After a cancellation run (FR-1-13, FR-1-15, 6.11 counting rules; D40-D42).

``on_run_finished`` is called when a ``cancel_subscription`` run finishes with ``success``:

* with a merchant confirmation email among the run's evidence, the subscription becomes ``cancelled`` and the
  ledger entry ``cancel:<subscription_id>`` is verified by that email;
* otherwise the subscription becomes ``cancel_in_progress``, the entry stays ``pending``, and a 48-hour watch starts.

``check`` (a tool, never a routine that acts) moves overdue watches to ``cancel_unconfirmed`` and, from
``next_charge_date + 3 days``, looks for a charge after the cancellation. A charge found there marks the
subscription ``charged_after_cancel``, reverses the ledger entry, and creates a refund request that the user still
has to confirm. The absence of a charge is not treated as proof: the bot may simply not have the statement yet.
Nothing here contacts a merchant, sends anything, or cancels anything.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from core.ledger import Ledger
from core.util import parse_date, parse_iso, to_iso, utcnow

CONFIRM_WINDOW = timedelta(hours=48)   # FR-1-13
CHARGE_CHECK_DELAY = timedelta(days=3)  # FR-1-15
CONFIRMING_KINDS = ("confirmation_email",)

NOTICE = {
    "unconfirmed": {
        "en": "{name}: no cancellation confirmation email arrived within 48 hours of cancelling on {date}. Please "
              "check the account, or forward the confirmation if you have it.",
        "zh": "{name}：{date} 取消后 48 小时内没有收到取消确认邮件。请登录账户核实，如果收到了确认邮件请转发给我。"},
    "charged_after_cancel": {
        "en": "{name} charged {amount} on {charge_date}, after the cancellation on {date}. I prepared a refund "
              "request for you to review.",
        "zh": "{name} 在 {date} 取消后，仍于 {charge_date} 扣费 {amount}。我准备了一份退款请求，请你确认。"},
    "no_charge_seen": {
        "en": "{name}: I see no charge after the cancellation on {date}, but I may not have the latest statement. "
              "Upload it, or forward the cancellation confirmation, to verify the saving.",
        "zh": "{name}：{date} 取消后我没有看到新的扣费，但可能还没有最新账单。上传账单或转发取消确认邮件即可核实这笔节省。"},
}


class FollowUpError(ValueError):
    pass


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


class CancelFollowUp:
    def __init__(self, store: Any, gate: Any, ledger: Ledger | None = None, now_fn: Callable[[], Any] | None = None):
        self.store, self.gate = store, gate
        self.ledger = ledger or Ledger(store)
        self.now_fn = now_fn or getattr(gate, "now_fn", utcnow)

    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.store.get_user().get("timezone") or "America/Los_Angeles")

    def _evidence(self, ids: list[str] | None) -> list[dict[str, Any]]:
        return [e for e in (self.store.get("evidence", i) for i in ids or []) if e]

    # -- after a successful run --------------------------------------------------------------------------------
    def on_run_finished(self, run: dict[str, Any]) -> dict[str, Any] | None:
        if run.get("outcome") != "success":
            return None
        req = self.store.get("action_requests", run["action_request_id"])
        if req is None or req["action_type"] != "cancel_subscription":
            return None
        existing = self.store.find("cancel_watches", {"action_request_id": req["id"]})
        if existing:
            return {"watch": existing[0], "already_recorded": True}
        sub = self.store.get("subscriptions", req["target_ref"])
        if sub is None:
            return None
        finished = parse_iso(run["finished_at"])
        cancelled_on = finished.astimezone(self._tz()).date()
        email = next((e for e in self._evidence(run.get("evidence_ids")) if e["kind"] in CONFIRMING_KINDS), None)
        charge_check_on = None
        if sub.get("next_charge_date"):
            nxt = parse_date(sub["next_charge_date"])
            if nxt >= cancelled_on:
                charge_check_on = (nxt + CHARGE_CHECK_DELAY).isoformat()
        entry = None
        monthly = sub.get("monthly_equiv_cents") or (sub.get("amount_cents") if sub.get("period") == "monthly" else None)
        if monthly:
            entry = self.ledger.add("subscriptions", "recurring", f"Cancel {sub['display_name']}", int(monthly),
                                    sub["id"], f"cancel:{sub['id']}", cancelled_on.isoformat(),
                                    merchant_id=sub.get("merchant_id"), evidence_ids=list(run.get("evidence_ids") or []),
                                    action_run_id=run["id"])
            if email and entry["status"] == "pending":
                entry = self.ledger.verify(entry["id"], f"evidence:{email['id']}", [email["id"]])
        self.store.update("subscriptions", sub["id"], {"status": "cancelled" if email else "cancel_in_progress"})
        watch = self.store.insert("cancel_watches", {
            "subscription_id": sub["id"], "action_request_id": req["id"], "run_id": run["id"],
            "executor": run["executor"], "cancelled_on": cancelled_on.isoformat(),
            "confirm_by": to_iso(finished + CONFIRM_WINDOW), "charge_check_on": charge_check_on,
            "status": "confirmed" if email else "awaiting_confirmation",
            "ledger_entry_id": entry["id"] if entry else None,
            "confirmation_evidence_id": email["id"] if email else None})
        out = {"watch": watch, "subscription_status": "cancelled" if email else "cancel_in_progress",
               "ledger_entry": entry}
        if not monthly:
            out["ledger_note"] = "no monthly amount is known for this subscription, so no saving was recorded"
        if not email:
            out["next_step"] = ("Watch for the merchant's confirmation email for 48 hours; record it with "
                                "record_evidence and call confirm_cancellation.")
        return out

    # -- confirmation -------------------------------------------------------------------------------------------
    def confirm(self, subscription_id: str, evidence_id: str) -> dict[str, Any]:
        watch = self._watch(subscription_id)
        if watch["status"] not in ("awaiting_confirmation", "unconfirmed"):
            raise FollowUpError(f"this cancellation is already {watch['status']}")
        ev = self.store.get("evidence", evidence_id)
        if ev is None or ev["kind"] not in CONFIRMING_KINDS:
            raise FollowUpError("only a merchant confirmation email (evidence kind confirmation_email) confirms a "
                                "cancellation")
        entry = None
        if watch["ledger_entry_id"]:
            entry = self.store.get("savings_ledger", watch["ledger_entry_id"])
            if entry["status"] == "pending":
                entry = self.ledger.verify(entry["id"], f"evidence:{ev['id']}", [ev["id"]])
        self.store.update("subscriptions", subscription_id, {"status": "cancelled"})
        watch = self.store.update("cancel_watches", watch["id"], {"status": "confirmed",
                                                                  "confirmation_evidence_id": ev["id"]})
        return {"watch": watch, "subscription_status": "cancelled", "ledger_entry": entry}

    def _watch(self, subscription_id: str) -> dict[str, Any]:
        rows = self.store.find("cancel_watches", {"subscription_id": subscription_id})
        if not rows:
            raise FollowUpError(f"no finished cancellation is being watched for {subscription_id}")
        return rows[-1]

    def status(self, subscription_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.find("cancel_watches", {"subscription_id": subscription_id} if subscription_id else None)

    # -- the periodic check (a tool; no routine acts on it) ---------------------------------------------------
    def check(self, lang: str = "en") -> dict[str, Any]:
        lang = lang if lang in ("en", "zh") else "en"
        now = self.now_fn()
        today = now.astimezone(self._tz()).date()
        notices = []
        for w in self.store.find("cancel_watches"):
            if w["status"] == "charged_after_cancel":
                continue
            sub = self.store.get("subscriptions", w["subscription_id"]) or {}
            fmt = {"name": sub.get("display_name", "?"), "date": w["cancelled_on"]}
            if w["status"] == "awaiting_confirmation" and now > parse_iso(w["confirm_by"]):
                w = self.store.update("cancel_watches", w["id"], {"status": "unconfirmed"})
                self.store.update("subscriptions", w["subscription_id"], {"status": "cancel_unconfirmed"})
                notices.append({"kind": "unconfirmed", "subscription_id": w["subscription_id"],
                                "message": NOTICE["unconfirmed"][lang].format(**fmt)})
            if w["charge_check_on"] and not w["charge_checked"] and today >= parse_date(w["charge_check_on"]):
                notices.append(self._charge_check(w, sub, fmt, lang))
        return {"notices": notices, "watches": self.store.find("cancel_watches")}

    def _charge_check(self, w: dict[str, Any], sub: dict[str, Any], fmt: dict[str, str], lang: str) -> dict[str, Any]:
        rows = self.store.conn.execute(
            "SELECT id FROM charges WHERE subscription_id = ? AND date > ? ORDER BY date",
            (w["subscription_id"], w["cancelled_on"])).fetchall()
        charges = [self.store.get("charges", r[0]) for r in rows]
        charges = [c for c in charges if c and c["amount_cents"] > 0]
        if not charges:
            self.store.update("cancel_watches", w["id"], {"charge_checked": 1})
            return {"kind": "no_charge_seen", "subscription_id": w["subscription_id"],
                    "message": NOTICE["no_charge_seen"][lang].format(**fmt)}
        charge = charges[0]
        name = sub.get("display_name", "the merchant")
        req = self.gate.create(
            "submit_claim", w["subscription_id"], f"Ask {name} to refund {_money(charge['amount_cents'])} charged on "
            f"{charge['date']}, after the cancellation on {w['cancelled_on']}",
            [f"Contact {name} support (see manual_cancel_guide for the reviewed contact)",
             f"Ask for a refund of {_money(charge['amount_cents'])} charged on {charge['date']}, after the "
             f"cancellation on {w['cancelled_on']}",
             "Share the cancellation evidence if they ask for it",
             "If they refuse, you can dispute the charge with your card issuer (the bot does not file disputes)"],
            constraints={"subscription_id": w["subscription_id"], "charge_id": charge["id"],
                         "reason": "charged_after_cancel", "cancel_run_id": w["run_id"]},
            expected_saving_cents=charge["amount_cents"], executor="manual", lang=lang,
            risk_notes="Charged after a cancellation. Nothing is sent until you confirm.")
        if w["ledger_entry_id"]:
            self.ledger.reverse(w["ledger_entry_id"], f"charged {_money(charge['amount_cents'])} on "
                                f"{charge['date']} after the cancellation", charge["date"])
        self.store.update("subscriptions", w["subscription_id"], {"status": "charged_after_cancel"})
        self.store.update("cancel_watches", w["id"], {"status": "charged_after_cancel", "charge_checked": 1,
                                                      "refund_request_id": req["id"]})
        return {"kind": "charged_after_cancel", "subscription_id": w["subscription_id"], "charge_id": charge["id"],
                "refund_request_id": req["id"], "confirmation_text": req.get("confirmation_text"),
                "message": NOTICE["charged_after_cancel"][lang].format(
                    **fmt, amount=_money(charge["amount_cents"]), charge_date=charge["date"])}
