"""Gmail scanner (FR-0-1).

The Gmail connector is called by the bot (``search_threads`` / ``get_thread``), not by this
process, so scanning is split into three steps that the MCP server exposes:

1. :func:`build_scan_plan` - targeted queries with ``after:`` bounds (13-month lookback on the first
   scan, then incremental from the per-account cursor).
2. :func:`ingest_messages` - classify + extract each fetched message and index it. Only structured
   fields, the message id, and a short redacted snippet are stored (PV-2). Re-ingesting the same
   message is a no-op (10.3 idempotency).
3. :func:`complete_scan` - advance the cursor.

:func:`run_scan` chains the three against any client object with a ``search(query)`` method; tests
use a fake client. Email content is untrusted: instructions inside messages are never executed.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Protocol

from . import privacy
from .classifier import Classifier
from .extractor import ExtractionSchemaError, extract, validate_llm_extraction
from .store import Store
from .util import CONFIG_DIR, parse_iso, to_iso, utcnow

SNIPPET_CHARS = 200


class GmailClient(Protocol):
    def search(self, query: str) -> list[dict[str, Any]]:
        """Return messages as dicts with id, thread_id, from, subject, date (ISO), body."""


def load_queries(path: str | None = None) -> dict[str, Any]:
    with open(path or os.path.join(CONFIG_DIR, "gmail_queries.json"), encoding="utf-8") as fh:
        return json.load(fh)


def months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) - months, 12)
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def build_scan_plan(store: Store, account: str, now: datetime | None = None,
                    modules: Iterable[str] | None = None) -> dict[str, Any]:
    now = now or utcnow()
    cfg = load_queries()
    cursor = (store.get_user().get("scan_cursor") or {}).get(account)
    if cursor:
        after = parse_iso(cursor).date() - timedelta(days=1)  # 1-day overlap; ingest is idempotent
        mode = "incremental"
    else:
        after = months_back(now.date(), cfg["initial_lookback_months"])
        mode = "initial"
    wanted = set(modules) if modules else None
    queries = []
    for q in cfg["queries"]:
        if wanted and not wanted.intersection(q["modules"]):
            continue
        queries.append({"id": q["id"], "q": f"{q['q']} after:{after.strftime('%Y/%m/%d')}"})
    return {"account": account, "mode": mode, "after": after.isoformat(), "scan_started_at": to_iso(now),
            "queries": queries}


def ingest_messages(store: Store, account: str, messages: list[dict[str, Any]],
                    classifier: Classifier | None = None) -> dict[str, Any]:
    classifier = classifier or Classifier()
    created, skipped, review = [], [], []
    for msg in messages:
        mid = str(msg.get("id") or msg.get("message_id") or "")
        if not mid:
            raise ValueError("every message needs an id")
        if store.find("source_messages", {"gmail_account": account, "message_id": mid}):
            skipped.append(mid)
            continue
        subject, body = msg.get("subject") or "", msg.get("body") or msg.get("snippet") or ""
        result = classifier.classify(subject, body, msg.get("from"))
        received = msg.get("date")
        ref = parse_iso(received).date() if received else utcnow().date()
        text = f"{subject}\n{body}"
        fields = extract(text, mid, ref)
        confidence = result.confidence
        if msg.get("llm_extraction") is not None:
            try:
                llm_fields, _dropped = validate_llm_extraction(msg["llm_extraction"], text, mid)
                fields.update(llm_fields)
            except ExtractionSchemaError:
                confidence = min(confidence, 0.49)  # 7.6: failed schema -> needs user review
        needs_review = confidence < 0.6
        store.insert("source_messages", {
            "gmail_account": account, "message_id": mid, "thread_id": msg.get("thread_id"),
            "from_addr": msg.get("from"), "subject": privacy.redact(subject)[:300],
            "received_at": received, "category": result.category, "merchant_id": result.merchant_id,
            "confidence": round(confidence, 3), "needs_review": needs_review,
            "extracted": {"fields": _redact_fields(fields), "snippet": privacy.redact(body)[:SNIPPET_CHARS]},
        })
        created.append({"message_id": mid, **result.as_dict(), "confidence": round(confidence, 3),
                        "needs_review": needs_review})
        if needs_review:
            review.append(mid)
    return {"ingested": len(created), "skipped_existing": len(skipped), "needs_review": review, "results": created}


def _redact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, f in fields.items():
        out[k] = {**f, "evidence_span": privacy.redact(f["evidence_span"])}
    return out


def complete_scan(store: Store, account: str, scan_started_at: str) -> dict[str, Any]:
    user = store.get_user()
    cursor = dict(user.get("scan_cursor") or {})
    cursor[account] = scan_started_at
    accounts = list(user.get("gmail_accounts") or [])
    if account not in accounts:
        accounts.append(account)
    store.update_user({"scan_cursor": cursor, "gmail_accounts": accounts})
    return {"account": account, "cursor": scan_started_at}


def run_scan(store: Store, client: GmailClient, account: str, now: datetime | None = None,
             classifier: Classifier | None = None) -> dict[str, Any]:
    plan = build_scan_plan(store, account, now)
    seen: dict[str, dict[str, Any]] = {}
    for q in plan["queries"]:
        for msg in client.search(q["q"]):
            seen.setdefault(str(msg["id"]), msg)
    result = ingest_messages(store, account, list(seen.values()), classifier)
    complete_scan(store, account, plan["scan_started_at"])
    return {"plan": plan, **result}
