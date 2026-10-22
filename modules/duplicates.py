"""Module 1b: duplicate subscriptions (FR-1-6, US-1-2, 10.1).

Rules:
  (a) same_merchant: the same merchant is active more than once, on different account emails, card endings or
      billing channels. When it is the same account on two channels (e.g. Apple + direct), both rows are kept
      and marked as a suspected duplicate for the user to confirm (10.1).
  (c) household: the same merchant for the user and a declared household member (only matched inside the user's
      own mail, PV-7). This suggests a family plan.
  (b) overlap: several active services in one category (by default >= 3 video streaming, >= 2 cloud storage).

(a) and (c) set ``duplicate_group_id``. (b) is advice only. Family plan prices are not in the registry and are not
looked up here, so the suggestion says what the user pays now and that the family price still needs checking.
Detection is idempotent: the same data always gives the same group ids.
"""
from __future__ import annotations

import hashlib
import string
from typing import Any

from core.merchant_resolver import MerchantResolver
from core.store import Store

COUNTED_STATUSES = ("active", "trial")
# FR-1-6(b) thresholds by registry category. The spec gives video >= 3 and cloud storage >= 2. Music and VPN
# are added because paying for two of them is almost always overlap.
OVERLAP_THRESHOLDS = {"streaming_video": 3, "cloud_storage": 2, "streaming_music": 2, "vpn": 2}
CATEGORY_LABELS = {"streaming_video": "video streaming", "cloud_storage": "cloud storage",
                   "streaming_music": "music streaming", "vpn": "VPN"}


def group_id(kind: str, key: str) -> str:
    """Deterministic, letters-only id (matches the store's generated-id format)."""
    digest = hashlib.sha256(f"{kind}:{key}".encode()).digest()
    return "dup_" + "".join(string.ascii_lowercase[b % 26] for b in digest[:16])


def _card_endings(store: Store, sub_id: str) -> set[str]:
    return {c["card_last4"] for c in store.find("charges", {"subscription_id": sub_id}) if c.get("card_last4")}


def detect(store: Store, resolver: MerchantResolver | None = None, apply: bool = True) -> dict[str, Any]:
    resolver = resolver or MerchantResolver()
    user = store.get_user()
    household = {e.lower() for e in (user.get("household_emails") or []) if isinstance(e, str)}
    subs = [s for s in store.find("subscriptions") if s["status"] in COUNTED_STATUSES]
    by_merchant: dict[str, list[dict[str, Any]]] = {}
    for s in subs:
        if s.get("merchant_id"):
            by_merchant.setdefault(s["merchant_id"], []).append(s)

    groups: list[dict[str, Any]] = []
    for merchant_id, rows in sorted(by_merchant.items()):
        m = resolver.get(merchant_id) or {}
        if len(rows) < 2 or m.get("category") == "aggregator":
            continue  # "service not identified" rows under Apple/PayPal are not the same service
        rows = sorted(rows, key=lambda r: (r.get("account_email") or "", r["billing_channel"], r["id"]))
        accounts = {(r.get("account_email") or "").lower() for r in rows}
        is_household = bool(accounts & household) and len(accounts) > 1
        same_account = len(accounts) == 1
        kind = "household" if is_household else "same_merchant"
        total = sum(r.get("monthly_equiv_cents") or 0 for r in rows)
        cheapest = min((r.get("monthly_equiv_cents") for r in rows if r.get("monthly_equiv_cents")), default=None)
        if same_account:
            advice = (f"{m.get('name', merchant_id)} appears to be billed more than once on the same account "
                      f"({', '.join(sorted({r['billing_channel'] for r in rows}))}). Please check whether this is "
                      "one subscription with two kinds of receipt, or a real double charge.")
            possible = total - cheapest if cheapest is not None else None
        else:
            advice = (f"You and others in your household pay for {m.get('name', merchant_id)} separately "
                      f"({len(rows)} subscriptions). Combining them into one family or shared plan may save money if "
                      "that plan costs less than what you pay in total. I have not checked the family plan price.")
            possible = None  # depends on a family price we do not know
        groups.append({
            "duplicate_group_id": group_id(kind, merchant_id), "rule": "c" if is_household else "a", "kind": kind,
            "suspected_same_subscription": same_account, "merchant_id": merchant_id,
            "name": m.get("name", merchant_id), "subscription_ids": [r["id"] for r in rows],
            "accounts": sorted(accounts), "card_endings": sorted(set().union(*(_card_endings(store, r["id"]) for r in rows))),
            "current_monthly_total_cents": total, "possible_monthly_saving_cents": possible, "advice": advice})

    overlaps = []
    for cat, threshold in sorted(OVERLAP_THRESHOLDS.items()):
        rows = [s for s in subs if (resolver.get(s.get("merchant_id") or "") or {}).get("category") == cat]
        services = sorted({s["merchant_id"] for s in rows})
        if len(services) >= threshold:
            rows.sort(key=lambda r: -(r.get("monthly_equiv_cents") or 0))
            overlaps.append({
                "rule": "b", "category": cat, "subscription_ids": [r["id"] for r in rows],
                "names": [r["display_name"] for r in rows],
                "monthly_total_cents": sum(r.get("monthly_equiv_cents") or 0 for r in rows),
                "advice": (f"You have {len(services)} {CATEGORY_LABELS.get(cat, cat)} services. Rotating them (keep one "
                           "or two, re-subscribe when a show you want comes out) usually saves money.")})

    changed = []
    if apply:
        wanted = {sid: g["duplicate_group_id"] for g in groups for sid in g["subscription_ids"]}
        for s in store.find("subscriptions"):
            target = wanted.get(s["id"])
            if s.get("duplicate_group_id") != target:
                store.update("subscriptions", s["id"], {"duplicate_group_id": target})
                changed.append(s["id"])
    return {"duplicate_groups": groups, "overlaps": overlaps, "changed_subscription_ids": changed}
