"""Merchant normalization (FR-0-4, 6.12).

Maps sender domains, names, and aliases (``netflix.com``, ``Netflix Inc``, ``NFLX``) to one
``merchant_registry`` id. Runtime-added merchants are flagged ``unverified`` (Q11).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .util import CONFIG_DIR

_SUFFIX = re.compile(r"\b(inc|llc|ltd|co|corp|corporation|company|usa|us|ab)\b\.?", re.I)
_NONWORD = re.compile(r"[^a-z0-9+]+")


def normalize_name(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"\.(com|net|org|tv)\b", "", value)
    value = _SUFFIX.sub(" ", value)
    return _NONWORD.sub(" ", value).strip()


def sender_domain(from_addr: str | None) -> str | None:
    if not from_addr:
        return None
    match = re.search(r"@([A-Za-z0-9.-]+)", from_addr)
    return match.group(1).lower().rstrip(".>") if match else None


class MerchantResolver:
    def __init__(self, registry_path: str | None = None):
        path = registry_path or os.path.join(CONFIG_DIR, "merchant_registry.json")
        with open(path, encoding="utf-8") as fh:
            self.doc = json.load(fh)
        self.merchants: dict[str, dict[str, Any]] = {m["id"]: m for m in self.doc["merchants"]}
        self._domains: dict[str, str] = {}
        self._aliases: dict[str, str] = {}
        for m in self.merchants.values():
            for d in m["email_domains"]:
                if d in self._domains:
                    raise ValueError(f"domain {d} claimed by {self._domains[d]} and {m['id']}")
                self._domains[d.lower()] = m["id"]
            for name in [m["name"], *m["aliases"]]:
                key = normalize_name(name)
                if key:
                    self._aliases.setdefault(key, m["id"])
        # longest alias first so "Apple Music" beats "Apple"
        self._alias_patterns = sorted(self._aliases.items(), key=lambda kv: -len(kv[0]))

    def get(self, merchant_id: str) -> dict[str, Any] | None:
        return self.merchants.get(merchant_id)

    def by_domain(self, domain: str | None) -> str | None:
        if not domain:
            return None
        parts = domain.lower().split(".")
        for i in range(len(parts) - 1):  # mail.netflix.com -> netflix.com
            hit = self._domains.get(".".join(parts[i:]))
            if hit:
                return hit
        return None

    def by_name(self, name: str | None) -> str | None:
        if not name:
            return None
        return self._aliases.get(normalize_name(name))

    def find_in_text(self, text: str | None) -> str | None:
        """Most specific merchant alias mentioned in free text (e.g. 'Calm Premium' in an Apple receipt)."""
        if not text:
            return None
        norm = f" {normalize_name(text)} "
        for alias, mid in self._alias_patterns:
            if len(alias) >= 4 and f" {alias} " in norm:
                return mid
        return None

    def resolve(self, name: str | None = None, from_addr: str | None = None,
                text: str | None = None) -> dict[str, Any] | None:
        """Resolve to {merchant_id, name, method, confidence, verification}.

        A billing aggregator sender (Apple, Google Play, PayPal, Stripe) is split into the real
        underlying service when the text names one (FR-1-2)."""
        mid, method, conf = self.by_name(name), "alias", 0.95
        if not mid:
            mid, method, conf = self.by_domain(sender_domain(from_addr)), "domain", 0.9
        if mid and self.merchants[mid]["category"] == "aggregator":
            inner = self.find_in_text(text)
            if inner and self.merchants[inner]["category"] != "aggregator":
                return self._out(inner, "aggregator_split", 0.85, billed_via=mid)
        if not mid:
            mid, method, conf = self.find_in_text(text), "text", 0.7
        if not mid:
            return None
        return self._out(mid, method, conf)

    def _out(self, mid: str, method: str, conf: float, billed_via: str | None = None) -> dict[str, Any]:
        m = self.merchants[mid]
        return {"merchant_id": mid, "name": m["name"], "method": method, "confidence": conf,
                "billed_via": billed_via or m.get("billed_via_parent"), "verification": m["verification"]}

    def add_runtime(self, merchant_id: str, name: str, category: str, email_domains: list[str] | None = None,
                    aliases: list[str] | None = None, source_urls: list[str] | None = None) -> dict[str, Any]:
        """Add a merchant discovered at runtime; always 'unverified' (Q11). In-memory only."""
        if merchant_id in self.merchants:
            raise ValueError(f"{merchant_id} already exists")
        m = {"id": merchant_id, "name": name, "aliases": aliases or [], "email_domains": email_domains or [],
             "category": category, "cancel_method": "web", "cancel_url": None, "support_phone": None,
             "cancel_notes": "", "retention_offer_known": False, "price_adjust_policy": None,
             "return_policy": None, "late_delivery_policy": None, "fare_rules": None, "billed_via_parent": None,
             "verification": "unverified", "updated_at": None, "source_urls": source_urls or []}
        self.merchants[merchant_id] = m
        for d in m["email_domains"]:
            self._domains.setdefault(d.lower(), merchant_id)
        for a in [name, *m["aliases"]]:
            self._aliases.setdefault(normalize_name(a), merchant_id)
        self._alias_patterns = sorted(self._aliases.items(), key=lambda kv: -len(kv[0]))
        return m
