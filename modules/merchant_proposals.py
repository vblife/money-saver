"""Merchant cancellation-method proposals (Q11, decided by Charles 2026-10-22; D32).

When the bot learns how a merchant can be cancelled (a URL, a phone number, a cancel email address, the steps), it
records that here as a PROPOSAL. Proposals live in their own table, apart from the reviewed merchant registry in
``config/merchant_registry.json``. They reach the registry only through a pull request that Developer reviews and
Reviewer merges; ``export`` produces the JSON document for that PR. Nothing in the execution path reads this table:
``core.execution_policy`` and ``modules.cancellation`` take cancel targets from the reviewed registry only, so an
unreviewed proposal can never drive a live cancellation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from core.execution_policy import EMAIL, E164

METHODS = ("web", "phone", "email", "chat", "app_store", "google_play", "other")
STATUSES = ("proposed", "exported", "withdrawn")
FIELDS = ("merchant_id", "merchant_name", "method", "cancel_url", "support_phone", "cancel_email", "steps",
          "source_urls", "observed_ref", "notes")
MAX_STEPS, MAX_STEP_CHARS, MAX_NOTES = 20, 300, 1000
EXPORT_FORMAT = "money-saver/merchant-method-proposals/v1"
REVIEW_RULE = ("Proposals only. Apply them to config/merchant_registry.json in a pull request that Developer reviews "
               "and Reviewer merges (Q11/D32). Check every value against the merchant's official site first.")


class ProposalError(ValueError):
    pass


def _https(url: Any) -> bool:
    return isinstance(url, str) and url.startswith("https://") and " " not in url and len(url) <= 500


def validate(p: Mapping[str, Any], registry: Mapping[str, Any]) -> dict[str, Any]:
    bad = set(p) - set(FIELDS)
    if bad:
        raise ProposalError(f"unknown proposal fields: {sorted(bad)}")
    out = {k: p.get(k) for k in FIELDS}
    mid = out["merchant_id"]
    if mid is not None and mid not in registry:
        raise ProposalError(f"merchant_id {mid!r} is not in the merchant registry; leave it empty for a new merchant")
    name = out["merchant_name"] or (registry[mid]["name"] if mid else None)
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ProposalError("merchant_name is required (at most 120 characters)")
    out["merchant_name"] = name.strip()
    if out["method"] not in METHODS:
        raise ProposalError(f"method must be one of {', '.join(METHODS)}")
    if out["cancel_url"] is not None and not _https(out["cancel_url"]):
        raise ProposalError("cancel_url must be an https:// URL")
    if out["support_phone"] is not None and not (isinstance(out["support_phone"], str)
                                                 and E164.match(out["support_phone"])):
        raise ProposalError("support_phone must be in E.164 form, e.g. +18005551234")
    if out["cancel_email"] is not None and not (isinstance(out["cancel_email"], str)
                                                and EMAIL.match(out["cancel_email"])):
        raise ProposalError("cancel_email must be an email address")
    steps = out["steps"] or []
    if not isinstance(steps, list) or len(steps) > MAX_STEPS or any(
            not isinstance(s, str) or not s.strip() or len(s) > MAX_STEP_CHARS for s in steps):
        raise ProposalError(f"steps must be a list of at most {MAX_STEPS} non-empty strings of at most "
                            f"{MAX_STEP_CHARS} characters")
    out["steps"] = [s.strip() for s in steps]
    urls = out["source_urls"] or []
    if not isinstance(urls, list) or len(urls) > 10 or not all(_https(u) for u in urls):
        raise ProposalError("source_urls must be a list of at most 10 https:// URLs")
    out["source_urls"] = urls
    if not any([out["cancel_url"], out["support_phone"], out["cancel_email"], out["steps"]]):
        raise ProposalError("a proposal needs at least one of cancel_url, support_phone, cancel_email or steps")
    notes = out["notes"] or ""
    if not isinstance(notes, str) or len(notes) > MAX_NOTES:
        raise ProposalError(f"notes must be text of at most {MAX_NOTES} characters")
    out["notes"] = notes
    if out["observed_ref"] is not None and not isinstance(out["observed_ref"], str):
        raise ProposalError("observed_ref must be a string")
    return out


def dedupe_key(p: Mapping[str, Any]) -> str:
    ident = p["merchant_id"] or p["merchant_name"].casefold()
    body = [ident, p["method"], p["cancel_url"], p["support_phone"], p["cancel_email"], p["steps"]]
    return "mmp:" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]


class MerchantProposals:
    def __init__(self, store: Any, registry: Mapping[str, Any]):
        self.store = store
        self.registry = registry

    def record(self, **fields: Any) -> dict[str, Any]:
        """Store a proposal (idempotent: the same method for the same merchant is stored once)."""
        p = validate(fields, self.registry)
        key = dedupe_key(p)
        existing = self.store.find("merchant_method_proposals", {"dedupe_key": key})
        if existing:
            return {**existing[0], "duplicate": True}
        return {**self.store.insert("merchant_method_proposals", {**p, "dedupe_key": key}), "duplicate": False}

    def record_from_log(self, log: Iterable[Any], observed_ref: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Auto-record ``{"learned_method": {...}}`` items an executor put in a run log. Errors never fail the run."""
        saved, errors = [], []
        for item in log or []:
            if not isinstance(item, dict) or not isinstance(item.get("learned_method"), dict):
                continue
            try:
                saved.append(self.record(**{"observed_ref": observed_ref, **item["learned_method"]}))
            except (ProposalError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        return saved, errors

    def list(self, status: str | None = None, merchant_id: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise ProposalError(f"status must be one of {', '.join(STATUSES)}")
        where = {k: v for k, v in (("status", status), ("merchant_id", merchant_id)) if v is not None}
        return self.store.find("merchant_method_proposals", where)

    def withdraw(self, proposal_id: str) -> dict[str, Any]:
        if self.store.get("merchant_method_proposals", proposal_id) is None:
            raise ProposalError(f"proposal {proposal_id} not found")
        return self.store.update("merchant_method_proposals", proposal_id, {"status": "withdrawn"})

    def export(self, proposal_ids: list[str] | None = None) -> dict[str, Any]:
        """JSON for Developer's registry PR, with the current registry values beside each proposal; marks the
        exported rows ``exported``. It changes nothing in the registry."""
        rows = (self.list(status="proposed") if proposal_ids is None
                else [self.store.get("merchant_method_proposals", i) for i in proposal_ids])
        missing = [i for i, r in zip(proposal_ids or [], rows) if r is None]
        if missing:
            raise ProposalError(f"proposals not found: {missing}")
        if any(r["status"] == "withdrawn" for r in rows):
            raise ProposalError("withdrawn proposals cannot be exported")
        items = []
        with self.store.transaction():
            for r in rows:
                cur = self.registry.get(r["merchant_id"] or "") or {}
                items.append({"proposal_id": r["id"], "merchant_id": r["merchant_id"],
                              "merchant_name": r["merchant_name"], "method": r["method"],
                              "proposed": {k: r[k] for k in ("cancel_url", "support_phone", "cancel_email", "steps")},
                              "registry_now": {k: cur.get(k) for k in ("cancel_method", "cancel_url", "support_phone",
                                                                       "cancel_email", "cancel_notes",
                                                                       "verification")} if cur else None,
                              "source_urls": r["source_urls"], "observed_ref": r["observed_ref"], "notes": r["notes"],
                              "recorded_at": r["created_at"]})
                if r["status"] == "proposed":
                    self.store.update("merchant_method_proposals", r["id"], {"status": "exported"})
        return {"format": EXPORT_FORMAT, "review_rule": REVIEW_RULE, "count": len(items), "proposals": items}
