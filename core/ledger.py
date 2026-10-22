"""Savings ledger (FR-0-7, FR-X-3, 6.11).

* Every successful action writes one entry; status starts ``pending`` (or ``estimated``).
* ``pending -> verified`` only with a verification reference (confirmation email, refund email,
  new bill). Verified amounts are immutable; corrections use a new ``reversed`` entry.
* ``dedupe_key`` is unique, so routines can safely re-run (10.3).
* Headline totals count only ``verified`` cash-like savings; travel credit and points are listed
  separately (Q10), and ``estimated`` is never merged into verified.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from .store import Store
from .util import parse_date, to_iso, utcnow

MODULES = ("subscriptions", "bills", "pre_purchase", "post_purchase", "travel", "recovery")
HEADLINE_FORMS = ("cash", "statement_credit", "gift_card")
MAX_RECURRING_MONTHS = 12


class LedgerError(ValueError):
    pass


def _months_between(start: date, month_start: date) -> int:
    return (month_start.year - start.year) * 12 + (month_start.month - start.month)


class Ledger:
    def __init__(self, store: Store):
        self.store = store

    def add(self, module: str, saving_type: str, description: str, amount_cents: int, source_ref: str,
            dedupe_key: str, occurred_at: str, status: str = "pending", merchant_id: str | None = None,
            form: str = "cash", points_amount: int | None = None, evidence_ids: list[str] | None = None,
            action_run_id: str | None = None, recurring_months: int | None = None) -> dict[str, Any]:
        """Add an entry, or return the existing one with the same dedupe_key (idempotent)."""
        if module not in MODULES:
            raise LedgerError(f"unknown module {module}")
        if status not in ("pending", "estimated"):
            raise LedgerError("new entries start as pending or estimated; verify them later")
        if not isinstance(amount_cents, int) or amount_cents < 0:
            raise LedgerError("amount_cents must be a non-negative integer number of cents")
        if saving_type == "recurring":
            months = min(recurring_months or MAX_RECURRING_MONTHS, MAX_RECURRING_MONTHS)
            annualized = amount_cents * 12
        elif saving_type == "one_time":
            months, annualized = None, None
        else:
            raise LedgerError("saving_type must be recurring or one_time")
        existing = self.store.find("savings_ledger", {"dedupe_key": dedupe_key})
        if existing:
            return existing[0]
        try:
            return self.store.insert("savings_ledger", {
                "module": module, "saving_type": saving_type, "description": description,
                "merchant_id": merchant_id, "amount_cents": amount_cents, "annualized_cents": annualized,
                "recurring_months": months, "form": form, "points_amount": points_amount, "status": status,
                "evidence_ids": evidence_ids or [], "source_ref": source_ref, "action_run_id": action_run_id,
                "dedupe_key": dedupe_key, "occurred_at": occurred_at[:10]})
        except sqlite3.IntegrityError:  # concurrent insert of the same key
            return self.store.find("savings_ledger", {"dedupe_key": dedupe_key})[0]

    def verify(self, entry_id: str, verification_ref: str, evidence_ids: list[str] | None = None,
               amount_cents: int | None = None) -> dict[str, Any]:
        entry = self.store.get("savings_ledger", entry_id)
        if entry is None:
            raise KeyError(entry_id)
        if not verification_ref:
            raise LedgerError("verification requires a verification_ref (e.g. refund email id, new bill)")
        if entry["status"] == "verified":
            return entry
        if entry["status"] != "pending":
            raise LedgerError(f"only pending entries can be verified (this one is {entry['status']})")
        changes: dict[str, Any] = {"status": "verified", "verification_ref": verification_ref,
                                   "verified_at": to_iso(utcnow()),
                                   "evidence_ids": list(entry["evidence_ids"]) + list(evidence_ids or [])}
        if amount_cents is not None:  # actual amount may differ from expected before verification
            changes["amount_cents"] = amount_cents
            if entry["saving_type"] == "recurring":
                changes["annualized_cents"] = amount_cents * 12
        return self.store.update("savings_ledger", entry_id, changes)

    def reverse(self, entry_id: str, reason: str, occurred_at: str | None = None) -> dict[str, Any]:
        """Correct a verified entry without editing it: add a ``reversed`` entry that offsets it."""
        entry = self.store.get("savings_ledger", entry_id)
        if entry is None:
            raise KeyError(entry_id)
        key = f"reverse:{entry['dedupe_key']}"
        existing = self.store.find("savings_ledger", {"dedupe_key": key})
        if existing:
            return existing[0]
        return self.store.insert("savings_ledger", {
            "module": entry["module"], "saving_type": entry["saving_type"],
            "description": f"Reversal of '{entry['description']}': {reason}", "merchant_id": entry["merchant_id"],
            "amount_cents": entry["amount_cents"], "annualized_cents": entry["annualized_cents"],
            "recurring_months": entry["recurring_months"], "form": entry["form"],
            "points_amount": entry["points_amount"], "status": "reversed", "source_ref": entry["source_ref"],
            "dedupe_key": key, "reverses_id": entry_id,
            "occurred_at": (occurred_at or to_iso(utcnow()))[:10]})

    def correct_verified_amount(self, entry_id: str, new_amount_cents: int) -> None:
        raise LedgerError("verified amounts are immutable; use reverse() and add a new entry (10.3)")

    # -- monthly math ---------------------------------------------------------------------
    def month_amount(self, entry: dict[str, Any], year: int, month: int) -> int:
        """Amount an entry contributes to a calendar month (6.11 counting rules)."""
        start = parse_date(entry["occurred_at"])
        month_start = date(year, month, 1)
        if entry["saving_type"] == "one_time":
            return entry["amount_cents"] if (start.year, start.month) == (year, month) else 0
        idx = _months_between(start, month_start)
        months = entry["recurring_months"] or MAX_RECURRING_MONTHS
        return entry["amount_cents"] if 0 <= idx < months else 0

    def month_summary(self, year: int, month: int) -> dict[str, Any]:
        """Verified vs pending vs estimated, by module; reversals subtract from verified."""
        totals = {"verified_cents": 0, "pending_cents": 0, "estimated_cents": 0,
                  "travel_credit_cents": 0, "points": 0, "by_module": {}, "entries": []}
        reversed_ids = {e["reverses_id"] for e in self.store.find("savings_ledger", {"status": "reversed"})}
        for entry in self.store.find("savings_ledger", order_by="occurred_at"):
            if entry["status"] == "reversed" or entry["id"] in reversed_ids:
                continue  # a reversal cancels its original; neither is counted
            amt = self.month_amount(entry, year, month)
            if entry["form"] == "points":
                start = parse_date(entry["occurred_at"])
                if (start.year, start.month) == (year, month):
                    totals["points"] += entry["points_amount"] or 0
                    totals["entries"].append({**entry, "month_cents": 0})
                continue
            if not amt:
                continue
            totals["entries"].append({**entry, "month_cents": amt})
            if entry["form"] == "travel_credit":
                totals["travel_credit_cents"] += amt
                continue
            totals[f"{entry['status']}_cents"] += amt
            if entry["status"] == "verified":
                totals["by_module"][entry["module"]] = totals["by_module"].get(entry["module"], 0) + amt
        return totals
