"""Module 1a: subscription inventory (spec 5.1: FR-1-2, FR-1-3, FR-1-4, FR-1-5, FR-1-16; US-1-1).

Builds ``Subscription`` and ``Charge`` rows from the indexed Gmail messages (``source_messages``). It uses
only the structured fields the scanner stored, each with an evidence span, and never full email bodies.

* Aggregator split (FR-1-2): the classifier already resolves an App Store / Google Play / PayPal receipt to
  the service it names. Here the sender decides the ``billing_channel``.
* Period (FR-1-3): with two or more charges, the median gap between charges; with one, the email wording.
* Next charge (FR-1-4): an explicit renewal / next-billing / trial-end date beats last charge + period.
* Last used (FR-1-5): always labelled "inferred". It comes from sign-in, activity and win-back emails.
* Price increases (FR-1-16): a higher charge than the previous one, or a price-change notice.

Running :meth:`SubscriptionInventory.build` again on the same data changes nothing (10.3). Statuses set by the
user or by the cancellation flow (ignored, cancel_*, paused, ...) are never overwritten here. Nothing in
this module contacts anyone.
"""
from __future__ import annotations

import calendar
import statistics
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.merchant_resolver import MerchantResolver, sender_domain
from core.store import Store
from core.util import parse_iso, utcnow

INVENTORY_CATEGORIES = ("subscription_receipt", "renewal_notice", "trial_start")
# Billing channel by the aggregator that sent the receipt (6.3 billing_channel).
# Stripe is a payment processor: the merchant bills the user directly, so cancellation goes to the merchant.
CHANNEL_BY_SENDER = {"apple": "apple", "google_play": "google_play", "paypal": "paypal", "stripe": "direct",
                     "amazon": "amazon"}
# FR-1-3: accepted gap bands (days) between consecutive charges.
INTERVAL_BANDS = (("weekly", 5, 9), ("monthly", 26, 35), ("quarterly", 84, 98), ("semiannual", 170, 195),
                  ("annual", 350, 380))
MONTHS_PER_PERIOD = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
# Statuses owned by the user or by the cancellation flow; the inventory never changes them.
PROTECTED_STATUSES = frozenset({"cancel_requested", "cancel_in_progress", "cancelled", "cancel_unconfirmed",
                                "charged_after_cancel", "paused", "ignored"})
ACTIVE_SIGNAL_DAYS = 60       # an "active" usage email older than this no longer counts
UNUSED_SIGNAL_DAYS = 180      # same for win-back ("we miss you") emails
EXPIRY_GRACE_DAYS = 3         # 10.1: no charge for more than 2 periods (+ grace) -> expired
UNKNOWN_PERIOD_EXPIRY_DAYS = 400
TIER_WORDS = ("Premium", "Standard", "Basic", "Plus", "Family", "Individual", "Duo", "Student", "Pro",
              "Essential", "Ultimate", "Ad-Supported", "Ad-Free")
DERIVED_FIELDS = ("display_name", "amount_cents", "currency", "period", "monthly_equiv_cents", "status", "is_trial",
                  "trial_end_date", "trial_converts_to_cents", "next_charge_date", "last_charge_date",
                  "last_used_signal", "last_used_evidence", "confidence", "source", "source_message_ids")


# --- date and money helpers -------------------------------------------------------------------
def add_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def add_period(d: date, period: str, n: int = 1) -> date:
    if period == "weekly":
        return d + timedelta(days=7 * n)
    if period in MONTHS_PER_PERIOD:
        return add_months(d, MONTHS_PER_PERIOD[period] * n)
    raise ValueError(f"cannot add an unknown period: {period}")


def monthly_equiv(amount_cents: int | None, period: str) -> int | None:
    if amount_cents is None or period == "unknown":
        return None
    if period == "weekly":
        return round(amount_cents * 52 / 12)
    return round(amount_cents / MONTHS_PER_PERIOD[period])


def infer_period(charge_dates: list[date]) -> str | None:
    """FR-1-3: period from the median gap between distinct charge dates (needs at least two)."""
    days = sorted(set(charge_dates))
    if len(days) < 2:
        return None
    gap = statistics.median((b - a).days for a, b in zip(days, days[1:]))
    for period, lo, hi in INTERVAL_BANDS:
        if lo <= gap <= hi:
            return period
    return None


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "America/Los_Angeles")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("America/Los_Angeles")


def _val(msg: dict[str, Any], name: str) -> Any:
    f = ((msg.get("extracted") or {}).get("fields") or {}).get(name)
    return f.get("value") if isinstance(f, dict) else None


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class SubscriptionInventory:
    def __init__(self, store: Store, resolver: MerchantResolver | None = None):
        self.store = store
        self.resolver = resolver or MerchantResolver()

    # -- helpers ---------------------------------------------------------------------------
    def _local_date(self, iso: str | None, tz: ZoneInfo) -> date | None:
        if not iso:
            return None
        try:
            return parse_iso(iso).astimezone(tz).date()
        except ValueError:
            return None

    def billing_channel(self, msg: dict[str, Any]) -> str:
        sender = self.resolver.by_domain(sender_domain(msg.get("from_addr")))
        merchant = self.resolver.get(msg.get("merchant_id") or "") or {}
        if sender in CHANNEL_BY_SENDER and sender != msg.get("merchant_id"):
            return CHANNEL_BY_SENDER[sender]
        parent = merchant.get("billed_via_parent")
        if parent in CHANNEL_BY_SENDER:
            return CHANNEL_BY_SENDER[parent]
        if merchant.get("category") == "aggregator":
            return CHANNEL_BY_SENDER.get(merchant["id"], "other")
        return "direct"

    def _display_name(self, merchant_id: str, msgs: list[dict[str, Any]]) -> str:
        m = self.resolver.get(merchant_id) or {}
        name = m.get("name") or merchant_id
        if m.get("category") == "aggregator":
            return f"{name} (service not identified)"
        for msg in sorted(msgs, key=lambda x: x.get("received_at") or "", reverse=True):
            text = f"{msg.get('subject') or ''} {(msg.get('extracted') or {}).get('snippet') or ''}".lower()
            for tier in TIER_WORDS:
                if f"{name.lower()} {tier.lower()}" in text:
                    return f"{name} {tier}"
        return name

    def _charge_date(self, msg: dict[str, Any], tz: ZoneInfo) -> date | None:
        received = self._local_date(msg.get("received_at"), tz)
        stated = _iso_date(_val(msg, "date"))
        nxt = _iso_date(_val(msg, "next_charge_date"))
        if stated and received and stated <= received and (received - stated).days <= 45 and stated != nxt:
            return stated
        return received or stated

    # -- inventory -------------------------------------------------------------------------
    def build(self, today: date | None = None) -> dict[str, Any]:
        st = self.store
        user = st.get_user()
        tz = _tz(user.get("timezone"))
        today = today or utcnow().astimezone(tz).date()
        msgs = st.find("source_messages", order_by="received_at")
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        unresolved: list[str] = []
        usage: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for msg in msgs:
            if _val(msg, "usage_signal") in ("active", "likely_unused") and msg.get("merchant_id"):
                usage.setdefault((msg["merchant_id"], msg["gmail_account"]), []).append(msg)
            if msg["category"] not in INVENTORY_CATEGORIES:
                continue
            if not msg.get("merchant_id"):
                unresolved.append(msg["message_id"])
                continue
            key = (msg["merchant_id"], msg["gmail_account"], self.billing_channel(msg))
            groups.setdefault(key, []).append(msg)

        result = {"created": [], "updated": [], "unchanged": [], "charges_added": 0, "unresolved_message_ids": unresolved}
        for key, group in sorted(groups.items()):
            sub, created, changed, added = self._upsert(key, group, usage.get(key[:2], []), tz, today)
            result["charges_added"] += added
            (result["created"] if created else result["updated"] if changed else result["unchanged"]).append(sub["id"])
        result["price_changes"] = self.price_changes()
        result["subscriptions"] = len(groups)
        return result

    def _existing(self, merchant_id: str, account: str, channel: str) -> dict[str, Any] | None:
        hits = self.store.find("subscriptions", {"merchant_id": merchant_id, "account_email": account,
                                                 "billing_channel": channel})
        return hits[0] if hits else None

    def _upsert(self, key, group, usage_msgs, tz, today):
        merchant_id, account, channel = key
        st = self.store
        existing = self._existing(merchant_id, account, channel)
        receipts = [m for m in group if m["category"] == "subscription_receipt" and _val(m, "amount_cents")]
        charges = []
        for m in receipts:
            d = self._charge_date(m, tz)
            if d:
                charges.append({"date": d, "amount_cents": int(_val(m, "amount_cents")), "message_id": m["message_id"],
                                "currency": _val(m, "currency") or "USD", "card_last4": _val(m, "last4"),
                                "merchant_raw": (m.get("subject") or "")[:120]})
        other_charges = []
        if existing:
            other_charges = [c for c in st.find("charges", {"subscription_id": existing["id"]}) if c["source"] != "gmail"]
        dated = sorted([(c["date"], c["amount_cents"]) for c in charges]
                       + [(_iso_date(c["date"]), c["amount_cents"]) for c in other_charges if _iso_date(c["date"])])
        last_charge = dated[-1][0] if dated else None

        # period (FR-1-3)
        period = infer_period([d for d, _ in dated])
        period_from_intervals = period is not None
        if not period:
            for m in sorted(group, key=lambda x: x.get("received_at") or "", reverse=True):
                if _val(m, "period") in MONTHS_PER_PERIOD or _val(m, "period") == "weekly":
                    period = _val(m, "period")
                    break
        period = period or "unknown"

        # trial
        trials = [m for m in group if m["category"] == "trial_start"]
        latest_trial = max(trials, key=lambda x: x.get("received_at") or "") if trials else None
        trial_day = self._local_date(latest_trial.get("received_at"), tz) if latest_trial else None
        is_trial = bool(latest_trial) and (last_charge is None or (trial_day is not None and last_charge < trial_day))
        trial_end = _iso_date(_val(latest_trial, "trial_end_date")) if is_trial else None
        trial_converts = _val(latest_trial, "amount_cents") if is_trial else None

        # amount: latest charge, else the announced renewal / trial conversion amount
        amount = dated[-1][1] if dated else None
        currency = charges[-1]["currency"] if charges else "USD"
        if amount is None:
            for m in sorted(group, key=lambda x: x.get("received_at") or "", reverse=True):
                if _val(m, "amount_cents"):
                    amount = int(_val(m, "amount_cents"))
                    currency = _val(m, "currency") or "USD"
                    break

        # next charge (FR-1-4): explicit date first, then last charge + period, rolled forward to today
        explicit = None
        for m in sorted(group, key=lambda x: x.get("received_at") or "", reverse=True):
            d = _iso_date(_val(m, "next_charge_date"))
            if d and (last_charge is None or d > last_charge):
                explicit = d
                break
        if is_trial and trial_end:
            nxt = trial_end
        elif explicit:
            nxt = explicit
        elif last_charge and period != "unknown":
            nxt = add_period(last_charge, period)
        else:
            nxt = None
        if nxt and period != "unknown" and not is_trial:
            for _ in range(1000):
                if nxt >= today:
                    break
                nxt = add_period(nxt, period)

        # status
        status = "trial" if is_trial else "active"
        if not is_trial and last_charge and not (explicit and explicit >= today):
            if period != "unknown":
                if add_period(last_charge, period, 2) + timedelta(days=EXPIRY_GRACE_DAYS) < today:
                    status = "expired"
            elif (today - last_charge).days > UNKNOWN_PERIOD_EXPIRY_DAYS:
                status = "expired"
        if status == "expired":
            nxt = None
        if existing and existing["status"] in PROTECTED_STATUSES:
            status = existing["status"]

        signal, evidence = self._last_used(usage_msgs, tz, today)
        conf = statistics.fmean(float(m["confidence"]) for m in group)
        if period_from_intervals:
            conf += 0.1
        if not dated:
            conf *= 0.9
        if self.resolver.get(merchant_id) and self.resolver.get(merchant_id)["category"] == "aggregator":
            conf *= 0.7
        sources = sorted(set((existing or {}).get("source") or []) | {"gmail"})
        derived = {
            "display_name": self._display_name(merchant_id, group), "amount_cents": amount, "currency": currency,
            "period": period, "monthly_equiv_cents": monthly_equiv(amount, period), "status": status,
            "is_trial": is_trial, "trial_end_date": trial_end.isoformat() if trial_end else None,
            "trial_converts_to_cents": int(trial_converts) if trial_converts else None,
            "next_charge_date": nxt.isoformat() if nxt else None,
            "last_charge_date": last_charge.isoformat() if last_charge else None,
            "last_used_signal": signal, "last_used_evidence": evidence, "confidence": round(min(conf, 0.99), 3),
            "source": sources,
            "source_message_ids": sorted(set(((existing or {}).get("source_message_ids") or []))
                                         | {m["message_id"] for m in group}),
        }
        created = changed = False
        if existing is None:
            sub = st.insert("subscriptions", {"merchant_id": merchant_id, "account_email": account,
                                              "billing_channel": channel, **derived})
            created = True
        else:
            diff = {k: v for k, v in derived.items() if _norm(existing.get(k)) != _norm(v)}
            sub = st.update("subscriptions", existing["id"], diff) if diff else existing
            changed = bool(diff)
        added = 0
        for c in charges:
            hit = st.find("charges", {"source": "gmail", "source_ref": c["message_id"]})
            if hit:
                if hit[0]["subscription_id"] != sub["id"]:
                    st.update("charges", hit[0]["id"], {"subscription_id": sub["id"]})
                continue
            st.insert("charges", {"subscription_id": sub["id"], "date": c["date"].isoformat(),
                                  "amount_cents": c["amount_cents"], "merchant_raw": c["merchant_raw"],
                                  "source": "gmail", "source_ref": c["message_id"], "card_last4": c["card_last4"]})
            added += 1
        return sub, created, changed, added

    @staticmethod
    def _last_used(usage_msgs, tz, today) -> tuple[str, str]:
        """FR-1-5. Never certain: the evidence text always says "inferred"."""
        for m in sorted(usage_msgs, key=lambda x: x.get("received_at") or "", reverse=True):
            d = parse_iso(m["received_at"]).astimezone(tz).date() if m.get("received_at") else None
            if d is None:
                continue
            signal = _val(m, "usage_signal")
            age = (today - d).days
            if signal == "active" and age <= ACTIVE_SIGNAL_DAYS:
                return "active", f'Inferred from the email "{m.get("subject") or ""}" on {d.isoformat()}'
            if signal == "likely_unused" and age <= UNUSED_SIGNAL_DAYS:
                return "likely_unused", (f'Inferred from the email "{m.get("subject") or ""}" on {d.isoformat()} '
                                         "(a win-back email suggests no recent use)")
            break
        return "unknown", "Inferred: no recent sign-in, activity or win-back emails found"

    # -- price increases (FR-1-16) ---------------------------------------------------------
    def price_changes(self) -> list[dict[str, Any]]:
        out = []
        for sub in self.store.find("subscriptions"):
            if sub["status"] in ("cancelled", "expired", "ignored"):
                continue
            charges = sorted(self.store.find("charges", {"subscription_id": sub["id"]}), key=lambda c: c["date"])
            if len(charges) >= 2 and charges[-1]["amount_cents"] > charges[-2]["amount_cents"]:
                out.append(self._price_item(sub, charges[-2]["amount_cents"], charges[-1]["amount_cents"],
                                            "charge_increase", [charges[-2]["source_ref"], charges[-1]["source_ref"]],
                                            charges[-1]["date"]))
                continue
            notice = self._price_notice(sub)
            if notice:
                out.append(notice)
        return out

    def _price_notice(self, sub):
        ids = sub.get("source_message_ids") or []
        if not ids:
            return None
        current = sub.get("amount_cents")
        rows = [r for r in self.store.find("source_messages", order_by="received_at DESC")
                if r["message_id"] in ids and r["gmail_account"] == sub["account_email"]]
        for r in rows:
            new = _val(r, "price_new_cents")
            if not new:
                continue
            old = _val(r, "price_old_cents") or current
            if old and new > old:
                return self._price_item(sub, old, new, "price_change_notice", [r["message_id"]],
                                        (r.get("received_at") or "")[:10])
        return None

    def _price_item(self, sub, old, new, kind, message_ids, when):
        return {"subscription_id": sub["id"], "display_name": sub["display_name"], "old_cents": old, "new_cents": new,
                "increase_cents": new - old, "period": sub["period"], "kind": kind, "seen_on": when,
                "evidence_message_ids": [m for m in message_ids if m], "alternatives": self.alternatives(sub)}

    def alternatives(self, sub) -> dict[str, Any]:
        """FR-1-16 alternatives. Peer names come from the registry only; their prices are not checked here."""
        m = self.resolver.get(sub.get("merchant_id") or "") or {}
        cat = m.get("category")
        peers = []
        if cat and cat not in ("aggregator", "retail"):
            peers = sorted(x["name"] for x in self.resolver.merchants.values()
                           if x["category"] == cat and x["id"] != m.get("id") and not x.get("billed_via_parent"))[:3]
        options = ["Ask the merchant for a cheaper tier, an ad-supported plan, or an annual plan",
                   "Keep it and accept the new price", "Cancel it (needs your confirmation)"]
        return {"options": options, "similar_services": peers,
                "note": "Prices of similar services were not checked; ask me to look them up before switching."}


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return round(v, 3)
    return v


# --- user-facing operations ---------------------------------------------------------------------
LIST_HIDDEN = ("cancelled", "expired", "ignored")
USER_STATUS_CHANGES = {"ignored", "active"}   # mark ignored (gift/company-paid, 10.1) or undo that
USER_DECISIONS = ("none", "keep", "cancel", "remind_later")
PERIODS = ("weekly", "monthly", "quarterly", "semiannual", "annual", "unknown")


def list_subscriptions(store: Store, include_inactive: bool = False) -> dict[str, Any]:
    subs = store.find("subscriptions")
    if not include_inactive:
        subs = [s for s in subs if s["status"] not in LIST_HIDDEN]
    subs.sort(key=lambda s: (-(s.get("monthly_equiv_cents") or 0), s["display_name"]))
    for s in subs:
        charges = sorted(store.find("charges", {"subscription_id": s["id"]}), key=lambda c: c["date"])
        s["charge_count"] = len(charges)
        s["recent_charges"] = [{"date": c["date"], "amount_cents": c["amount_cents"]} for c in charges[-3:]]
    counted = [s for s in subs if s["status"] in ("active", "trial") and s.get("monthly_equiv_cents")]
    total = sum(s["monthly_equiv_cents"] for s in counted if s["status"] == "active")
    return {"subscriptions": subs, "monthly_total_cents": total, "annual_total_cents": total * 12,
            "unknown_amount": [s["id"] for s in subs if s.get("monthly_equiv_cents") is None],
            "needs_review": [s["id"] for s in subs if (s.get("confidence") or 0) < 0.6]}


def update_subscription(store: Store, subscription_id: str, user_decision: str | None = None,
                        status: str | None = None) -> dict[str, Any]:
    sub = store.get("subscriptions", subscription_id)
    if sub is None:
        raise KeyError(f"unknown subscription {subscription_id}")
    changes: dict[str, Any] = {}
    if user_decision is not None:
        if user_decision not in USER_DECISIONS:
            raise ValueError(f"user_decision must be one of {USER_DECISIONS}")
        changes["user_decision"] = user_decision
    if status is not None:
        if status not in USER_STATUS_CHANGES:
            raise ValueError("status can only be set to 'ignored' or back to 'active' here; "
                             "cancellation goes through the confirmation gate")
        if status == "active" and sub["status"] != "ignored":
            raise ValueError("only an ignored subscription can be set back to active")
        if sub["status"] in PROTECTED_STATUSES - {"ignored"}:
            raise ValueError(f"subscription is {sub['status']}; it is managed by the cancellation flow")
        changes["status"] = status
    if not changes:
        raise ValueError("nothing to change")
    return store.update("subscriptions", subscription_id, changes)


def add_manual_subscription(store: Store, display_name: str, amount_cents: int, period: str,
                            next_charge_date: str | None = None, merchant_id: str | None = None,
                            account_email: str | None = None, billing_channel: str = "direct") -> dict[str, Any]:
    """A subscription the user tells us about (source ``user_added``)."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or not 0 <= amount_cents <= 10_000_000:
        raise ValueError("amount_cents must be an integer between 0 and 10,000,000")
    if next_charge_date is not None:
        date.fromisoformat(next_charge_date)
    if not display_name or len(display_name) > 120:
        raise ValueError("display_name must be 1-120 characters")
    return store.insert("subscriptions", {
        "merchant_id": merchant_id, "display_name": display_name, "billing_channel": billing_channel,
        "account_email": account_email, "amount_cents": amount_cents, "period": period,
        "monthly_equiv_cents": monthly_equiv(amount_cents, period), "next_charge_date": next_charge_date,
        "status": "active", "confidence": 1.0, "source": ["user_added"], "last_used_signal": "unknown",
        "last_used_evidence": "Inferred: added by you; no usage emails checked yet"})
