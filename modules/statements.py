"""Module 1c: card statement upload (FR-0-8, US-1-3, PV-1, PV-3).

The user drops a statement (PDF or CSV) into the upload inbox, the same directory as the evidence inbox (D14):
* The file is opened with the D14 checks. Only regular files inside the inbox are accepted: no symlinks, no hard
  links, re-verified through the open descriptor.
* The transactions are parsed: PDFs through poppler ``pdftotext`` (from the verified descriptor, via stdin),
  CSVs with the stdlib.
* **Only likely recurring lines are kept**: a known subscription merchant, a subscription keyword, or the same
  descriptor repeating at a subscription-like interval. Groceries, fuel, restaurants and other one-off purchases
  are dropped and never stored.
* A kept line keeps only date, redacted descriptor, amount and card last 4 (PV-3). Account numbers, balances,
  addresses and card numbers in the statement are never stored.
* **The original file is deleted by default** after it is read, whether or not parsing worked (PV-3). The user
  can ask to keep it.
Kept lines become ``charges`` (source ``card_statement``) linked to subscriptions. A matching Gmail subscription is
reused; otherwise a statement-only subscription is created. Re-importing the same statement adds nothing.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import string
import subprocess
from datetime import date, timedelta
from typing import Any

from core import privacy
from core.privacy import ForbiddenDataError
from core.evidence import EvidenceStore
from core.merchant_resolver import MerchantResolver, normalize_name
from core.store import Store
from modules.subscriptions import (EXPIRY_GRACE_DAYS, INTERVAL_BANDS, PROTECTED_STATUSES, SubscriptionInventory,
                                   _tz, add_period, infer_period, looks_like_fixed_expense, monthly_equiv)
from core.util import utcnow

MAX_BYTES = 10 * 1024 * 1024
MAX_LINES = 20_000
PDFTOTEXT_TIMEOUT_S = 30
SUBSCRIPTION_CATEGORIES_EXCLUDED = {"retail", "food_delivery"}  # one-off purchases unless a keyword says otherwise
KEYWORDS = re.compile(r"\b(subscription|subscr|membership|member fee|monthly|annual fee|recurring|renewal|prime)\b", re.I)
AMOUNT_TOLERANCE = 0.10
# Card payments and credits by their descriptor (D26). These lines are never charges, whatever their sign, and their
# sign tells which convention a single-amount CSV uses. The rules are precise on purpose: a merchant descriptor
# that merely contains PMT or PAYMENT ("GOOGLE *YOUTUBE PMT", "SPOTIFY PAYMENT") is a charge, not a card payment.
# Bare "credit" is not matched either ("credit monitoring" is a real subscription).
_PAY = r"(?:payments?|pymts?|pmts?)"
PAYMENT_RX = re.compile(
    r"\b(?:autopay|auto[\s-]?pay(?:ment)?|automatic\s+" + _PAY + r"|thank\s+you|"
    r"(?:online|mobile|web|internet|electronic|phone|ach|bank|e)[\s-]?" + _PAY + r"|epay(?:ment)?|"
    r"(?:credit\s+card|card|cc|loan|mortgage|auto\s+loan|student\s+loan)\s+" + _PAY + r"|"
    + _PAY + r"\s+(?:received|recvd|rcvd|posted|thank)|"
    r"refund|return(?:ed)?\s+(?:item|merchandise|purchase)|statement\s+credit|credit\s+(?:adj\w*|voucher|balance)|"
    r"cash\s*back\s+(?:reward|redemption)\w*)\b", re.I)
# A line that is nothing but payment words, balance words, reference numbers and punctuation, with at least one
# payment word: "PAYMENT", "PMT 0412 REF 77", "BILL PAY", "DIRECTPAY FULL BALANCE". Any merchant name in the line
# ("SPOTIFY PAYMENT") makes it a charge.
_PAY_WORD = r"(?:payments?|pymts?|pmts?|pay|directpay|autopay|epay(?:ment)?|e-?pay(?:ment)?)"
_PAYMENT_FILLER = (r"(?:bill|direct|full|balance|statement|stmt|minimum|min|current|new|amount|amt|due|online|mobile|"
                   r"web|internet|ach|electronic|received|recvd|rcvd|posted|thank|you|from|chk|checking|savings|acct|"
                   r"account|ref|conf|confirmation|no|id|nbr|num|number|to|card)")
_PAYMENT_ONLY_RX = re.compile(r"^(?=.*\b" + _PAY_WORD + r"\b)(?:\W|\d|\b(?:" + _PAY_WORD + "|" + _PAYMENT_FILLER +
                              r")\b)*$", re.I)
# Aggregator prefixes whose rest names the merchant. "PAYPAL *PAYMENT" (no merchant) is a payment; "PAYPAL *HULU
# PAYMENT" is a charge. APPLE.COM/BILL and AMZN DIGITAL name the merchant themselves, so they are always charges.
_MERCHANT_AFTER_PREFIX = {"paypal", "google_play", "direct"}


def _payment_only(text: str) -> bool:
    return bool(text.strip() and _PAYMENT_ONLY_RX.match(text.strip()))


def is_payment_descriptor(desc: str) -> bool:
    """A card/loan payment or a credit (D26). A line billed through a merchant or aggregator prefix (PAYPAL *,
    GOOGLE *, APPLE.COM/BILL, ...) is a charge unless it says refund or statement credit, or, for PayPal, Google
    and Square-style prefixes, nothing but payment words follow the prefix."""
    text = desc or ""
    for rx, channel in PREFIXES:
        m = rx.match(text)
        if m:
            if re.search(r"\b(?:refund|statement\s+credit)\b", text, re.I):
                return True
            return channel in _MERCHANT_AFTER_PREFIX and _payment_only(m.group("rest"))
    return bool(PAYMENT_RX.search(text) or _payment_only(text))


# Billing-aggregator / processor prefixes in descriptors -> (billing_channel, keep-the-rest-as-merchant-text)
PREFIXES = [
    (re.compile(r"^\s*paypal\s*\*\s*(?P<rest>.+)$", re.I), "paypal"),
    (re.compile(r"^\s*apple\.com/bill\b(?P<rest>.*)$", re.I), "apple"),
    (re.compile(r"^\s*google\s*\*\s*(?P<rest>.+)$", re.I), "google_play"),
    (re.compile(r"^\s*(?:amzn|amazon)\s+(?:digital|prime|channels)\b(?P<rest>.*)$", re.I), "amazon"),
    (re.compile(r"^\s*(?:sq|tst|sp)\s*\*\s*(?P<rest>.+)$", re.I), "direct"),
]
AGGREGATOR_FOR_CHANNEL = {"paypal": "paypal", "apple": "apple", "google_play": "google_play", "amazon": "amazon"}
_DATE_FULL = re.compile(r"(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{2,4})")
_PERIOD = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:-|–|to|through)\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
_CLOSING = re.compile(r"closing date\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
# A PDF transaction line: date [posting date] description amount
_PDF_LINE = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+)?(?P<desc>.+?)\s+"
    r"(?P<amt>-?\(?\$?-?[\d,]+\.\d{2}\)?)(?:\s*(?P<cr>CR))?\s*$", re.I)
HEADER_ALIASES = {
    "date": ("transaction date", "trans. date", "trans date", "date", "posted date", "post date", "posting date"),
    "description": ("description", "merchant", "payee", "name", "details", "merchant name"),
    "amount": ("amount", "transaction amount"),
    "debit": ("debit", "debit amount", "charges"),
    "credit": ("credit", "credit amount", "payments"),
    "type": ("type", "transaction type"),
    "card": ("card", "card no.", "card number", "card no", "account", "card member"),
}


class StatementError(ValueError):
    """The statement could not be read or parsed."""


# --- parsing -------------------------------------------------------------------------------------------
def _year4(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def _parse_date(text: str, period: tuple[date, date] | None, default_year: int) -> date | None:
    text = text.strip()
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text[:10])
        m = _DATE_FULL.match(text)
        if m:
            return date(_year4(m["y"]), int(m["m"]), int(m["d"]))
        m = re.match(r"^(\d{1,2})/(\d{1,2})$", text)
        if m:
            mo, d = int(m[1]), int(m[2])
            if period:
                for y in sorted({period[0].year, period[1].year}):
                    cand = date(y, mo, d)
                    if period[0] - timedelta(days=7) <= cand <= period[1] + timedelta(days=7):
                        return cand
            return date(default_year, mo, d)
    except ValueError:
        return None
    return None


def _cents(text: str) -> int | None:
    t = text.strip().replace("$", "").replace(",", "")
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("()-+ ")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", t):
        return None
    whole, _, frac = t.partition(".")
    cents = int(whole) * 100 + int((frac + "00")[:2])
    return -cents if neg else cents


def statement_period(text: str) -> tuple[date, date] | None:
    m = _PERIOD.search(text)
    if m:
        a, b = (_parse_date(x, None, 2000) for x in m.groups())
        if a and b and a <= b:
            return a, b
    m = _CLOSING.search(text)
    if m:
        end = _parse_date(m[1], None, 2000)
        if end:
            return end - timedelta(days=35), end
    return None


def parse_pdf_text(text: str, default_year: int) -> list[dict[str, Any]]:
    """Lines from ``pdftotext -layout`` output. Positive amounts are charges; negatives / CR are credits."""
    period = statement_period(text)
    out = []
    for line in text.splitlines()[:MAX_LINES]:
        m = _PDF_LINE.match(line)
        if not m:
            continue
        d = _parse_date(m["date"], period, period[1].year if period else default_year)
        amt = _cents(m["amt"])
        if d is None or amt is None:
            continue
        if m["cr"] or is_payment_descriptor(m["desc"]):
            amt = -abs(amt)
        out.append({"date": d, "description": m["desc"].strip(), "amount_cents": amt, "card_last4": None})
    return out


def _header_map(header: list[str]) -> dict[str, int]:
    norm = [h.strip().lower() for h in header]
    found: dict[str, int] = {}
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:  # aliases are in preference order (transaction date before posted date)
            if alias in norm:
                found[key] = norm.index(alias)
                break
    return found


def _csv_rows(text: str) -> list[list[str]]:
    rows = []
    try:
        for row in csv.reader(io.StringIO(text)):
            rows.append(row)
            if len(rows) > MAX_LINES:
                break
    except csv.Error as exc:  # e.g. a field over csv.field_size_limit() (131,072 chars), or a NUL byte
        msg = "a field is too long" if "field larger than" in str(exc) else "the file is malformed"
        raise StatementError(f"could not read the CSV ({msg}, near line {len(rows) + 1})") from None
    return rows


def parse_csv_text(text: str, default_year: int) -> list[dict[str, Any]]:
    rows = _csv_rows(text)
    start = next((i for i, r in enumerate(rows[:20]) if {"date", "description"} <= set(_header_map(r))), None)
    if start is None:
        raise StatementError("CSV needs a header row with a date column and a description column")
    cols = _header_map(rows[start])
    if "amount" not in cols and "debit" not in cols:
        raise StatementError("CSV needs an amount column (or debit/credit columns)")
    parsed = []
    for r in rows[start + 1:]:
        if len(r) <= max(cols.values()):
            continue
        d = _parse_date(r[cols["date"]], None, default_year)
        if d is None:
            continue
        kind = r[cols["type"]].strip().lower() if "type" in cols else ""
        if "debit" in cols and r[cols["debit"]].strip():
            amt = abs(_cents(r[cols["debit"]]) or 0) or None
        elif "credit" in cols and r[cols["credit"]].strip() and "amount" not in cols:
            amt = -abs(_cents(r[cols["credit"]]) or 0)
        else:
            amt = _cents(r[cols["amount"]]) if "amount" in cols else None
        if amt is None:
            continue
        last4 = None
        if "card" in cols:
            digits = re.sub(r"\D", "", r[cols["card"]])
            last4 = digits[-4:] if len(digits) >= 4 else None
        parsed.append({"date": d, "description": r[cols["description"]].strip(), "amount_cents": amt,
                       "card_last4": last4, "type": kind})
    if "amount" in cols and "debit" not in cols and _purchases_are_negative(parsed):
        for p in parsed:
            p["amount_cents"] = -p["amount_cents"]
    for p in parsed:
        if p.get("type") in CREDIT_TYPES or is_payment_descriptor(p["description"]):
            p["amount_cents"] = -abs(p["amount_cents"])
        p.pop("type", None)
    return parsed


SALE_TYPES = ("sale", "purchase", "debit")
CREDIT_TYPES = ("payment", "return", "refund", "credit", "adjustment")


def _purchases_are_negative(parsed: list[dict[str, Any]]) -> bool:
    """Sign convention of a single amount column (D26). Never decided by a majority vote over all rows:
    1. a type column: the sign of the rows typed sale/purchase/debit, if they all agree;
    2. else payment/credit descriptors (AUTOPAY, PAYMENT, THANK YOU, REFUND, ...), if they all agree: negative
       payments mean the usual convention; positive payments mean purchases are negative, unless every other row
       is positive too (an unsigned export);
    3. else purchases are negative only if every other row is negative.
    Anything ambiguous keeps the usual convention (charges positive). Payment lines are dropped either way."""
    typed = [p["amount_cents"] < 0 for p in parsed if p.get("type") in SALE_TYPES and p["amount_cents"]]
    if typed:
        return all(typed)
    credits = [p["amount_cents"] > 0 for p in parsed if (p.get("type") in CREDIT_TYPES or is_payment_descriptor(p["description"]))
               and p["amount_cents"]]
    rest = [p["amount_cents"] < 0 for p in parsed if not is_payment_descriptor(p["description"]) and p["amount_cents"]]
    if credits and not any(credits):
        return False
    if credits and all(credits):
        return any(rest)
    return bool(rest) and all(rest)


def pdf_to_text(data: bytes) -> str:
    """Run poppler's pdftotext on the bytes (stdin to stdout), so no second path-based open is needed."""
    try:
        res = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"], input=data, capture_output=True,
                             timeout=PDFTOTEXT_TIMEOUT_S, check=False)
    except FileNotFoundError:
        raise StatementError("pdftotext (poppler) is not installed") from None
    except subprocess.TimeoutExpired:
        raise StatementError("pdftotext timed out") from None
    if res.returncode != 0:
        raise StatementError("could not read the PDF (is it a text PDF, not a scan?)")
    return res.stdout.decode("utf-8", "replace")


# --- recurring filter ------------------------------------------------------------------------------------
def descriptor_key(desc: str) -> str:
    t = desc.lower()
    t = re.sub(r"#\s*\d+|\b\d{3}[-. ]?\d{3}[-. ]?\d{4}\b|\b\d{3,}\b", " ", t)   # store numbers, phone numbers
    t = re.sub(r"\s+[a-z]{2}\s*$", " ", t)                                    # trailing state code
    return normalize_name(t)


def _letters(digest: bytes, n: int = 16) -> str:
    return "".join(string.ascii_lowercase[b % 26] for b in digest[:n])


class StatementImporter:
    def __init__(self, store: Store, resolver: MerchantResolver | None = None,
                 inbox_dirs: list[str] | None = None, evidence_root: str | None = None):
        self.store = store
        self.resolver = resolver or MerchantResolver()
        self.inbox = EvidenceStore(store, evidence_root, inbox_dirs)

    def classify_line(self, desc: str) -> dict[str, Any]:
        channel, text = "direct", desc
        for rx, ch in PREFIXES:
            m = rx.match(desc)
            if m:
                channel, text = ch, (m.group("rest") or "").strip()
                break
        merchant_id = self.resolver.find_in_text(text) or self.resolver.by_name(text)
        if not merchant_id and channel in AGGREGATOR_FOR_CHANNEL:
            merchant_id = AGGREGATOR_FOR_CHANNEL[channel]
        if not merchant_id:
            merchant_id = self.resolver.find_in_text(desc)
        m = self.resolver.get(merchant_id or "") or {}
        if m.get("category") == "aggregator" and m["id"] in CHANNEL_BY_ID:
            channel = CHANNEL_BY_ID[m["id"]]
        known_sub = bool(m) and m.get("category") not in SUBSCRIPTION_CATEGORIES_EXCLUDED
        return {"merchant_id": merchant_id, "channel": channel, "known_subscription_merchant": known_sub,
                "keyword": bool(KEYWORDS.search(desc)), "key": descriptor_key(desc),
                "fixed_expense": not merchant_id and looks_like_fixed_expense(desc)}

    def select_recurring(self, lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        charges = [dict(line, **self.classify_line(line["description"])) for line in lines if line["amount_cents"] > 0]
        by_key: dict[str, list[dict[str, Any]]] = {}
        for c in charges:
            by_key.setdefault(c["merchant_id"] or c["key"], []).append(c)
        keep = []
        for group in by_key.values():
            repeated = False
            if len(group) >= 2:
                amounts = [c["amount_cents"] for c in group]
                close = max(amounts) <= min(amounts) * (1 + AMOUNT_TOLERANCE)
                period = infer_period([c["date"] for c in group])
                repeated = close and period is not None
            for c in group:
                if c["known_subscription_merchant"] or c["keyword"] or repeated:
                    c["reason"] = ("known subscription merchant" if c["known_subscription_merchant"]
                                   else "subscription wording" if c["keyword"] else "repeats at a regular interval")
                    if c["fixed_expense"]:
                        c["reason"] += "; regular fixed expense (rent, HOA, mortgage), not a subscription"
                    keep.append(c)
        dropped = len(lines) - len(keep)
        return sorted(keep, key=lambda c: (c["date"], c["description"])), dropped

    # -- linking --------------------------------------------------------------------------------
    def _find_subscription(self, c: dict[str, Any]) -> dict[str, Any] | None:
        if c["merchant_id"]:
            rows = [s for s in self.store.find("subscriptions", {"merchant_id": c["merchant_id"],
                                                                 "billing_channel": c["channel"]})]
        else:
            rows = [s for s in self.store.find("subscriptions") if not s.get("merchant_id")
                    and descriptor_key(s["display_name"]) == c["key"]]
        if not rows:
            return None
        if c.get("card_last4"):
            for s in rows:
                if any(ch.get("card_last4") == c["card_last4"] for ch in self.store.find("charges", {"subscription_id": s["id"]})):
                    return s
        rows.sort(key=lambda s: (abs((s.get("amount_cents") or 0) - c["amount_cents"]), s["created_at"]))
        return rows[0]

    def _source_ref(self, c: dict[str, Any], n: int) -> str:
        digest = hashlib.sha256(f"{c['key']}|{c['date'].isoformat()}|{c['amount_cents']}|{n}".encode()).digest()
        return "stmt_" + _letters(digest)

    def import_lines(self, lines: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
        """Store the kept lines. One transaction: an error on any line leaves the database as it was."""
        with self.store.transaction():
            return self._import_lines(lines, today)

    def _import_lines(self, lines: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
        tz = _tz(self.store.get_user().get("timezone"))
        today = today or utcnow().astimezone(tz).date()
        keep, dropped = self.select_recurring(lines)
        seen: dict[tuple, int] = {}
        added, created, linked, touched = 0, [], set(), set()
        for c in keep:
            ident = (c["key"], c["date"], c["amount_cents"])
            seen[ident] = seen.get(ident, 0) + 1
            ref = self._source_ref(c, seen[ident])
            if self.store.find("charges", {"source": "card_statement", "source_ref": ref}):
                continue
            sub = self._find_subscription(c)
            if sub is None:
                m = self.resolver.get(c["merchant_id"] or "") or {}
                name = m.get("name") or privacy.redact(c["description"])[:80]
                if m.get("category") == "aggregator":
                    name = f"{m['name']} (service not identified)"
                sub = self.store.insert("subscriptions", {
                    "merchant_id": c["merchant_id"], "display_name": name, "billing_channel": c["channel"],
                    "account_email": None, "amount_cents": c["amount_cents"], "status": "active",
                    "confidence": 0.7 if c["merchant_id"] else 0.3 if c["fixed_expense"] else 0.5,
                    "source": ["card_statement"],
                    "last_used_signal": "unknown",
                    "last_used_evidence": "Inferred: found on a card statement; no usage emails checked"})
                created.append(sub["id"])
            else:
                linked.add(sub["id"])
                if "card_statement" not in (sub.get("source") or []):
                    self.store.update("subscriptions", sub["id"], {"source": sorted(set(sub["source"]) | {"card_statement"})})
            self.store.insert("charges", {"subscription_id": sub["id"], "date": c["date"].isoformat(),
                                          "amount_cents": c["amount_cents"],
                                          "merchant_raw": privacy.redact(c["description"])[:120],
                                          "source": "card_statement", "source_ref": ref, "card_last4": c.get("card_last4")})
            touched.add(sub["id"])
            added += 1
        for sub_id in sorted(touched):
            self.refresh(sub_id, today)
        gmail_backed = [s for s in touched if "gmail" in (self.store.get("subscriptions", s).get("source") or [])]
        if gmail_backed:
            SubscriptionInventory(self.store, self.resolver).build(today)
        return {"lines_read": len(lines), "kept": len(keep), "dropped": dropped, "charges_added": added,
                "subscriptions_created": created, "subscriptions_linked": sorted(linked - set(created)),
                "not_subscriptions": sum(1 for c in keep if c["fixed_expense"]),
                "kept_preview": [{"date": c["date"].isoformat(), "description": privacy.redact(c["description"])[:80],
                                  "amount_cents": c["amount_cents"], "reason": c["reason"],
                                  "not_a_subscription": c["fixed_expense"]} for c in keep[:50]]}

    def refresh(self, sub_id: str, today: date) -> None:
        """Derived fields for a statement-only subscription (Gmail-backed rows are refreshed by the inventory)."""
        sub = self.store.get("subscriptions", sub_id)
        if "gmail" in (sub.get("source") or []):
            return
        charges = sorted(self.store.find("charges", {"subscription_id": sub_id}), key=lambda c: c["date"])
        if not charges:
            return
        dates = [date.fromisoformat(c["date"]) for c in charges]
        period = infer_period(dates) or sub.get("period") or "unknown"
        last = dates[-1]
        nxt = None
        status = sub["status"]
        if period != "unknown":
            nxt = add_period(last, period)
            while nxt < today:
                nxt = add_period(nxt, period)
            if status not in PROTECTED_STATUSES:
                status = "expired" if add_period(last, period, 2) + timedelta(days=EXPIRY_GRACE_DAYS) < today else "active"
        changes = {"amount_cents": charges[-1]["amount_cents"], "period": period, "last_charge_date": last.isoformat(),
                   "monthly_equiv_cents": monthly_equiv(charges[-1]["amount_cents"], period),
                   "next_charge_date": None if status == "expired" else (nxt.isoformat() if nxt else None),
                   "status": status}
        diff = {k: v for k, v in changes.items() if sub.get(k) != v}
        if diff:
            self.store.update("subscriptions", sub_id, diff)

    # -- files ---------------------------------------------------------------------------------
    def import_file(self, path: str, keep_original: bool = False, default_year: int | None = None,
                    card_last4: str | None = None, today: date | None = None) -> dict[str, Any]:
        if card_last4 is not None and not re.fullmatch(r"\d{4}", str(card_last4)):
            raise StatementError("card_last4 must be exactly 4 digits")
        fd, real = self.inbox.open_inbox_file(path)
        try:
            with os.fdopen(fd, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
        finally:
            deleted = False if keep_original else self._delete(real)
        if len(data) > MAX_BYTES:
            raise StatementError(f"statement is larger than {MAX_BYTES // (1024 * 1024)} MB"
                                 f"{'; the upload was deleted' if deleted else ''}")
        year = default_year or (today or utcnow().date()).year
        try:
            if data[:5] == b"%PDF-":
                lines, kind = parse_pdf_text(pdf_to_text(data), year), "pdf"
            else:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise StatementError("not a PDF or a UTF-8 CSV file") from None
                lines, kind = parse_csv_text(text, year), "csv"
        except StatementError as exc:
            raise StatementError(f"{exc}{'; the upload was deleted' if deleted else ''}") from None
        finally:
            del data
        if card_last4:
            for line in lines:
                line["card_last4"] = line.get("card_last4") or card_last4
        try:
            result = self.import_lines(lines, today)
        except ForbiddenDataError as exc:
            raise StatementError(f"nothing was imported ({exc}){'; the upload was deleted' if deleted else ''}") from None
        return {"file_type": kind, **result, "original_deleted": deleted,
                "note": "Only likely recurring lines were kept; the rest of the statement was not stored."}

    def _delete(self, real: str) -> bool:
        """PV-3: remove the uploaded original (only ever inside the upload inbox)."""
        if not self.inbox._inside_allowed(os.path.realpath(real)):
            return False
        try:
            os.unlink(real)
            return True
        except FileNotFoundError:
            return True


CHANNEL_BY_ID = {"apple": "apple", "google_play": "google_play", "paypal": "paypal", "amazon": "amazon"}
