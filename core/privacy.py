"""Privacy guards (spec 8.3 and 9).

* Never store full card numbers, CVVs, full SSNs, passwords, or bank credentials.
* Mask account numbers to the last 4 digits and addresses to city + ZIP in logs and reports.
"""
from __future__ import annotations

import re

CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
SECRET_KV_RE = re.compile(r"\b(password|passcode|cvv|cvc)\s*[:=]\s*\S+|\b(pin|routing number)\s*[:=]\s*\d{3,}", re.I)
CARD_IIN_RE = re.compile(r"^(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|2[2-7]\d{14}|3[47]\d{13}|6(?:011|5\d\d)\d{12})$")


class ForbiddenDataError(ValueError):
    """Raised when a value would persist data the spec forbids us to store."""


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_card_numbers(text: str) -> list[str]:
    hits = []
    for match in CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if CARD_IIN_RE.match(digits) and luhn_ok(digits):
            hits.append(match.group(0))
    return hits


def find_forbidden(text: str) -> list[str]:
    reasons = []
    if find_card_numbers(text):
        reasons.append("full_card_number")
    if SSN_RE.search(text):
        reasons.append("full_ssn")
    if SECRET_KV_RE.search(text):
        reasons.append("credential")
    return reasons


def assert_no_forbidden_secrets(text: str, field: str = "value") -> None:
    reasons = find_forbidden(text)
    if reasons:
        raise ForbiddenDataError(f"refusing to store {', '.join(reasons)} in {field}")


def redact(text: str) -> str:
    """Redact forbidden data from free text (e.g. email snippets) before it is stored."""
    for hit in find_card_numbers(text):
        digits = re.sub(r"\D", "", hit)
        text = text.replace(hit, f"[card ****{digits[-4:]}]")
    text = SSN_RE.sub("[ssn redacted]", text)
    text = SECRET_KV_RE.sub(lambda m: f"{m.group(1) or m.group(2)}: [redacted]", text)
    return text


def mask_account(value: str | None) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    tail = (digits or value)[-4:]
    return f"****{tail}"


def mask_address(city: str | None, zip_code: str | None) -> str:
    """PV-4: show only city + ZIP."""
    return ", ".join(p for p in (city, zip_code) if p)


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"
