"""Rule-based email classifier (FR-0-2).

Outputs ``{category, merchant_id, confidence}``. This is the deterministic baseline and
validation layer; an LLM pass (7.6) may refine low-confidence results, but its output is
schema-checked by :mod:`core.extractor`. Email content is untrusted data: nothing here acts on
instructions found in a message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .merchant_resolver import MerchantResolver

CATEGORIES = ("subscription_receipt", "trial_start", "renewal_notice", "order_confirmation", "shipping_update",
              "delivery", "refund", "travel_booking", "travel_change", "bill", "insurance_policy", "gift_card",
              "loyalty_statement", "settlement_notice", "other")
REVIEW_THRESHOLD = 0.6  # 10.1: below this, put the message in the "needs user review" list
MIN_SCORE = 2.0         # below this, the category is "other"

# (pattern, weight). Subject matches count double.
RULES: dict[str, list[tuple[str, float]]] = {
    "subscription_receipt": [
        (r"\breceipt\b", 2), (r"payment (received|confirmation)|thanks for your payment|thank you for your payment", 2.5),
        (r"\binvoice\b", 2), (r"(monthly|annual|yearly) (plan|subscription)|\bplan:", 1.5), (r"(/|per |a )(month|mo|year)\b|billed (monthly|annually)", 1.5),
        (r"subscription is active|thanks for subscribing|membership payment|subscription payment", 2.5),
        (r"next (billing|payment) date|next payment of", 1.5), (r"you were charged|amount paid|total charged", 1.5),
    ],
    "trial_start": [
        (r"free trial|\btrial (has )?start|trial is active|start your [\w-]+ trial|trial period|trial end", 4),
        (r"\btrial\b", 2), (r"\b\d+ days? free|watch free for|first (\d+ days|month) (is |are )?free|free month", 2.5),
        (r"first payment of", 1.5), (r"after (your|the) (free )?trial", 2),
    ],
    "renewal_notice": [
        (r"will (automatically )?renew|renews (on|soon)|\brenewing\b|set to renew|renew soon|upcoming renewal", 3.5),
        (r"auto-?renew", 3), (r"price (change|increase)|will increase to|price is changing", 3.5), (r"renewal amount|renewed for", 2.5),
    ],
    "order_confirmation": [
        (r"order confirm|thanks for your order|thank you for your order|order (has been )?(placed|received|confirmed)|we (got|received) your order", 3.5),
        (r"order (#|number|no\.?)\s*[\w-]+|\border\s+\d[\d-]+", 1.5), (r"order total|subtotal|thanks for shopping", 1.5),
        (r"(ready for pickup|estimated delivery|arrives by)", 0.5),
    ],
    "shipping_update": [
        (r"has shipped|\bshipped\b|on (its|the) way|out for delivery|shipment|shipping update", 3.5),
        (r"tracking (number)?|track your package|carrier:", 2),
        (r"(package|shipment|delivery|order) (is )?(running late|delayed)|running late|scheduled for delivery", 3.5),
    ],
    "delivery": [
        (r"\b(was|been|were|item) delivered\b|delivered:|^delivered|package delivered|delivery (is )?complete", 4.5),
    ],
    "refund": [
        (r"\brefund(ed)?\b|return (has been )?(received|processed)|credit (issued|of)|price adjustment", 4), (r"original (form of )?payment", 1),
    ],
    "travel_booking": [
        (r"flight confirmation|itinerary|record locator|confirmation code|eticket|e-ticket", 3.5),
        (r"reservation (is )?(confirmed|booked)|booking confirmation|your stay is booked|hotel reservation", 3.5),
        (r"check-in:|check-out:|\bflight\s+(\w{2}\s?)?\d{2,4}\b|departs|departing|\bseats?\b \d", 1.5), (r"free cancellation|cancel for free", 1.5),
        (r"confirmation (number|#)|your trip|all set for your (trip|flight|stay)", 1.5),
    ],
    "travel_change": [
        (r"schedule change|itinerary (has )?changed|change to your itinerary|updated flight time|departure time has changed", 4),
        (r"flight (is |has been |\w+ )?(delayed|canceled|cancelled)|delay (update|notice)|depart \d+ hours late|cancellation notice", 4.5),
        (r"rebooked|new departure time|meal vouchers?", 2),
    ],
    "bill": [
        (r"bill is (ready|now available)|your (\w+ )?bill\b|statement is (ready|available|now available)|statement for", 3.5),
        (r"amount due|total (amount )?due|payment (of \$[\d.,]+ )?is due|payment due|due date", 3), (r"autopay", 2.5),
        (r"account ending|internet service|wireless|lines?\b", 0.5),
    ],
    "insurance_policy": [
        (r"(?<!privacy )\bpolicy\b", 2.5), (r"insurance", 2.5), (r"declarations|premium|deductible|coverage|id cards", 2.5),
    ],
    "gift_card": [
        (r"gift ?card|egift", 5), (r"(account|card) balance", 1),
    ],
    "loyalty_statement": [
        (r"\bpoints?\b|\bmiles\b", 3), (r"rewards?|loyalty|member(ship)? (account )?summary|elite status|account summary", 2.5),
        (r"expire|expiring", 1),
    ],
    "settlement_notice": [
        (r"settlement|class action|class members?|claim form|court-authorized|litigation|claims administrator", 4.5),
    ],
}
_COMPILED = {cat: [(re.compile(p, re.I | re.M), w) for p, w in rules] for cat, rules in RULES.items()}

# Category hints from the registry, used as a light tiebreaker.
_REGISTRY_HINT = {"retail": {"order_confirmation", "shipping_update", "delivery", "refund"}}


@dataclass
class Classification:
    category: str
    merchant_id: str | None
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        return self.confidence < REVIEW_THRESHOLD

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "merchant": self.merchant_id, "confidence": round(self.confidence, 3),
                "needs_review": self.needs_review}


class Classifier:
    def __init__(self, resolver: MerchantResolver | None = None):
        self.resolver = resolver or MerchantResolver()

    def score(self, subject: str, body: str) -> dict[str, float]:
        scores = {}
        for cat, rules in _COMPILED.items():
            s = 0.0
            for rx, w in rules:
                if rx.search(subject):
                    s += 2 * w
                elif rx.search(body):
                    s += w
            scores[cat] = s
        # Disambiguation: trial wording dominates a receipt/renewal that mentions a trial.
        if scores["trial_start"] >= 8:
            scores["renewal_notice"] *= 0.5
        # A "refund" in a cancelled-flight notice is a travel change, not a refund.
        if scores["travel_change"] >= 8:
            scores["refund"] *= 0.3
            scores["travel_booking"] *= 0.5
        # Shipping notices often repeat the order number; deliveries mention returns.
        if scores["delivery"] >= 9:
            scores["refund"] *= 0.3
            scores["shipping_update"] *= 0.5
        if scores["shipping_update"] >= 7:
            scores["order_confirmation"] *= 0.5
        if scores["gift_card"] >= 5:
            scores["loyalty_statement"] *= 0.5
            scores["subscription_receipt"] *= 0.5
        if scores["insurance_policy"] >= 7:
            scores["renewal_notice"] *= 0.5
            scores["bill"] *= 0.5
        if scores["settlement_notice"] >= 9:
            scores["refund"] *= 0.3
        return scores

    def classify(self, subject: str = "", body: str = "", from_addr: str | None = None) -> Classification:
        subject, body = subject or "", body or ""
        scores = self.score(subject, body)
        resolved = self.resolver.resolve(from_addr=from_addr, text=f"{subject}\n{body}")
        merchant_id = resolved["merchant_id"] if resolved else None
        if merchant_id:
            hint = _REGISTRY_HINT.get(self.resolver.get(merchant_id)["category"], set())
            for cat in hint:
                if scores[cat]:
                    scores[cat] += 0.5
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        (top, top_s), (_, second_s) = ranked[0], ranked[1]
        if top_s < MIN_SCORE:
            return Classification("other", merchant_id, 0.7 if top_s == 0 else 0.5, scores)
        margin = (top_s - second_s) / top_s
        confidence = min(0.99, (0.45 + 0.55 * margin) * min(1.0, top_s / 7))
        return Classification(top, merchant_id, confidence, scores)
