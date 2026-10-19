"""Evidence capture (FR-0-6, 6.10).

Files (screenshots, transcripts, receipts) are copied under ``<evidence_dir>/<entity>/<id>/`` with a
SHA-256 digest. Confirmation emails are stored by reference only (message id + short redacted
snippet; PV-2 forbids persisting full email bodies).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from typing import Any

from . import privacy
from .store import Store
from .util import default_evidence_dir, new_id, to_iso, utcnow

KINDS = ("screenshot", "confirmation_email", "call_transcript", "call_recording_url", "form_receipt",
         "chat_transcript", "statement_line")
FILE_KINDS = {"screenshot", "call_transcript", "form_receipt", "chat_transcript"}
SNIPPET_MAX = 280
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class EvidenceStore:
    def __init__(self, store: Store, root: str | None = None):
        self.store = store
        self.root = root or default_evidence_dir()

    def _entity_dir(self, related_ref: str, evidence_id: str) -> str:
        entity = _SAFE.sub("_", related_ref.split("_", 1)[0] or "misc")
        return os.path.join(self.root, entity, _SAFE.sub("_", related_ref), evidence_id)

    def add_file(self, kind: str, src_path: str, related_ref: str, notes: str = "") -> dict[str, Any]:
        if kind not in FILE_KINDS:
            raise ValueError(f"{kind} is not a file evidence kind")
        if not os.path.isfile(src_path):
            raise FileNotFoundError(src_path)
        evidence_id = new_id("evd")
        dest_dir = self._entity_dir(related_ref, evidence_id)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(src_path))
        shutil.copy2(src_path, dest)
        return self.store.insert("evidence", {
            "id": evidence_id, "kind": kind, "path_or_ref": dest, "sha256": sha256_file(dest),
            "captured_at": to_iso(utcnow()), "related_ref": related_ref, "notes": privacy.redact(notes)})

    def add_email(self, message_id: str, related_ref: str, snippet: str = "") -> dict[str, Any]:
        snippet = privacy.redact(snippet)[:SNIPPET_MAX]
        return self.store.insert("evidence", {
            "kind": "confirmation_email", "path_or_ref": f"gmail:{message_id}",
            "sha256": hashlib.sha256(message_id.encode()).hexdigest(), "captured_at": to_iso(utcnow()),
            "related_ref": related_ref, "notes": snippet})

    def add_ref(self, kind: str, ref: str, related_ref: str, notes: str = "") -> dict[str, Any]:
        """Reference evidence such as a call recording URL or a statement line."""
        if kind not in KINDS or kind in FILE_KINDS or kind == "confirmation_email":
            raise ValueError(f"use add_file/add_email for {kind}")
        return self.store.insert("evidence", {
            "kind": kind, "path_or_ref": privacy.redact(ref), "sha256": hashlib.sha256(ref.encode()).hexdigest(),
            "captured_at": to_iso(utcnow()), "related_ref": related_ref, "notes": privacy.redact(notes)})

    def verify(self, evidence_id: str) -> bool:
        """True when a file still matches its recorded digest (reference kinds are always True)."""
        ev = self.store.get("evidence", evidence_id)
        if ev is None:
            raise KeyError(evidence_id)
        if ev["kind"] not in FILE_KINDS:
            return True
        return os.path.isfile(ev["path_or_ref"]) and sha256_file(ev["path_or_ref"]) == ev["sha256"]

    def wipe_files(self) -> None:
        """PV-8: remove all evidence files."""
        if os.path.isdir(self.root):
            shutil.rmtree(self.root)
