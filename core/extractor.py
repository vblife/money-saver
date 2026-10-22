"""Structured extraction (FR-0-3) and LLM-output validation (7.6).

Every extracted field is ``{"value": ..., "evidence_span": <exact text>, "source_message_id": id}``.
A field whose evidence span cannot be found in the original text is dropped, so amounts and
dates are never invented. Money becomes integer cents.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH_RX = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_PATTERNS = [
    re.compile(rf"\b{_MONTH_RX}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),  # Oct 3, 2026
    re.compile(rf"\b{_MONTH_RX}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!,?\s*\d)", re.I),      # Oct 23
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),                                      # 2026-10-03
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),                                  # 10/03/2026
]
AMOUNT_RX = re.compile(r"(?P<cur>\$|USD\s?|€|EUR\s?)\s?(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)(?:\s?(?P<cur2>USD))?")
PRIMARY_HINT = re.compile(
    r"(total|charged|amount (paid|due)|payment of|price|refund of|credit of|renew\w*|premium|due|paid|balance|plan)", re.I)
PERIOD_RULES = [
    ("weekly", re.compile(r"(/|per |a )week\b|\bweekly\b", re.I)),
    ("monthly", re.compile(r"(/|per |a )(month|mo)\b|\bmonthly\b|billed monthly", re.I)),
    ("quarterly", re.compile(r"\bquarterly\b|(/|per )quarter\b", re.I)),
    ("semiannual", re.compile(r"\b(6|six)[- ]months?\b|semi-?annual", re.I)),
    ("annual", re.compile(r"(/|per |a )(year|yr)\b|\bannual(ly)?\b|\byearly\b", re.I)),
]
ORDER_RX = re.compile(r"\border\s*(?:#|number|no\.?)?\s*:?\s*#?\s*(?P<v>[A-Z0-9][A-Z0-9-]{4,})", re.I)
CONF_RX = re.compile(r"(?i:confirmation (?:code|number|#)|record locator|confirmation\s*#?)\s*:?\s*#?\s*(?P<v>[A-Z0-9]{5,10})\b")
LAST4_RX = re.compile(r"(?:ending(?: in)?|ends in|last 4[: ]*)\s*(?P<v>\d{4})\b", re.I)
TRIAL_DAYS_RX = re.compile(r"\b(?P<v>\d{1,2})[- ]day (?:free )?trial|\b(?P<v2>\d{1,2}) days (?:free|for free)", re.I)

FIELD_TYPES = {"amount_cents": int, "currency": str, "period": str, "date": str, "order_number": str,
               "confirmation_code": str, "last4": str, "trial_days": int, "trial_end_date": str,
               "next_charge_date": str, "merchant": str}
PERIODS = ("weekly", "monthly", "quarterly", "semiannual", "annual", "unknown")


def to_cents(num: str) -> int:
    whole, _, frac = num.replace(",", "").partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def _field(value: Any, span: str, mid: str | None) -> dict[str, Any]:
    return {"value": value, "evidence_span": span, "source_message_id": mid}


def _infer_year(month: int, day: int, ref: date) -> date:
    candidate = date(ref.year, month, day)
    if candidate < ref - timedelta(days=60):
        candidate = date(ref.year + 1, month, day)
    return candidate


def find_dates(text: str, ref: date) -> list[tuple[date, str, int]]:
    """(date, span, position) for every date found, in text order."""
    found: list[tuple[date, str, int]] = []
    taken: list[range] = []
    for i, rx in enumerate(DATE_PATTERNS):
        for m in rx.finditer(text):
            if any(m.start() in r for r in taken):
                continue
            try:
                if i == 0:
                    d = date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
                elif i == 1:
                    d = _infer_year(MONTHS[m.group(1)[:3].lower()], int(m.group(2)), ref)
                elif i == 2:
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                else:
                    d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
            found.append((d, m.group(0), m.start()))
            taken.append(range(m.start(), m.end()))
    return sorted(found, key=lambda t: t[2])


def find_amounts(text: str) -> list[tuple[int, str, str, int]]:
    out = []
    for m in AMOUNT_RX.finditer(text):
        cur = (m.group("cur") or "").strip()
        currency = "EUR" if cur in ("€", "EUR") else "USD"
        out.append((to_cents(m.group("num")), currency, m.group(0).strip(), m.start()))
    return out


def _date_near(text: str, dates, keywords: str) -> tuple[date, str] | None:
    rx = re.compile(keywords, re.I)
    best = None
    for m in rx.finditer(text):
        for d, span, pos in dates:
            dist = pos - m.end()
            if 0 <= dist <= 60 and (best is None or dist < best[0]):
                best = (dist, d, span)
    return (best[1], best[2]) if best else None


def extract(text: str, source_message_id: str | None = None, ref_date: date | None = None) -> dict[str, Any]:
    """Rule-based extraction. Returns only fields with an exact evidence span."""
    ref = ref_date or date.today()
    out: dict[str, Any] = {}
    amounts = find_amounts(text)
    if amounts:
        primary = amounts[0]
        for amt in amounts:
            window = text[max(0, amt[3] - 40):amt[3]]
            if PRIMARY_HINT.search(window):
                primary = amt
                break
        out["amount_cents"] = _field(primary[0], primary[2], source_message_id)
        out["currency"] = _field(primary[1], primary[2], source_message_id)
    for period, rx in PERIOD_RULES:
        m = rx.search(text)
        if m:
            out["period"] = _field(period, m.group(0), source_message_id)
            break
    dates = find_dates(text, ref)
    if dates:
        out["date"] = _field(dates[0][0].isoformat(), dates[0][1], source_message_id)
    trial_end = _date_near(text, dates, r"trial (period )?ends?( on)?|trial end date:?|after your trial ends on|cancel before|first payment of \$[\d.]+ will be on")
    if trial_end:
        out["trial_end_date"] = _field(trial_end[0].isoformat(), trial_end[1], source_message_id)
    nxt = _date_near(text, dates, r"next (billing|payment) date:?|renews? on|will (automatically )?renew on|billing cycle on|next payment of \$[\d.]+ will be on")
    if nxt:
        out["next_charge_date"] = _field(nxt[0].isoformat(), nxt[1], source_message_id)
    for name, rx in (("order_number", ORDER_RX), ("confirmation_code", CONF_RX), ("last4", LAST4_RX)):
        m = rx.search(text)
        if m and (name != "order_number" or any(ch.isdigit() for ch in m.group("v"))):
            out[name] = _field(m.group("v"), m.group(0), source_message_id)
    m = TRIAL_DAYS_RX.search(text)
    if m:
        out["trial_days"] = _field(int(m.group("v") or m.group("v2")), m.group(0), source_message_id)
    return out


# --- LLM output validation (7.6) ---------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


class ExtractionSchemaError(ValueError):
    pass


def validate_llm_extraction(payload: Any, text: str, source_message_id: str | None = None) -> tuple[dict, list[str]]:
    """Validate an LLM extraction against the schema and the source text.

    Raises :class:`ExtractionSchemaError` if the payload is not a dict of fields (the caller retries
    once, then marks confidence < 0.5). Individual fields whose evidence span is missing from the
    text, or whose amount/date does not match its span, are dropped and listed in the second value.
    """
    if not isinstance(payload, dict):
        raise ExtractionSchemaError("extraction must be a JSON object")
    clean, dropped = {}, []
    hay = _norm(text)
    for name, item in payload.items():
        if name not in FIELD_TYPES:
            dropped.append(f"{name}: unknown field")
            continue
        if not isinstance(item, dict) or "value" not in item or "evidence_span" not in item:
            raise ExtractionSchemaError(f"{name} must be an object with value and evidence_span")
        value, span = item["value"], item["evidence_span"]
        if not isinstance(span, str) or not span.strip() or _norm(span) not in hay:
            dropped.append(f"{name}: evidence_span not found in source text")
            continue
        if not isinstance(value, FIELD_TYPES[name]) or isinstance(value, bool):
            dropped.append(f"{name}: wrong type")
            continue
        if name == "amount_cents" and value not in [a[0] for a in find_amounts(span)]:
            dropped.append(f"{name}: value does not match evidence_span")
            continue
        if name in ("date", "trial_end_date", "next_charge_date"):
            try:
                d = date.fromisoformat(value)
            except ValueError:
                dropped.append(f"{name}: not an ISO date")
                continue
            if d not in [x[0] for x in find_dates(span, d)]:
                dropped.append(f"{name}: value does not match evidence_span")
                continue
        if name == "period" and value not in PERIODS:
            dropped.append(f"{name}: unknown period")
            continue
        clean[name] = _field(value, span, source_message_id)
    return clean, dropped
