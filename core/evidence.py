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
import stat
from typing import Any

from . import privacy
from .store import Store
from .util import default_evidence_dir, default_evidence_inbox_dirs, new_id, to_iso, utcnow

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


class EvidencePathError(ValueError):
    """Raised when a file outside the allowed evidence inbox (or a symlink) is offered as evidence."""


class EvidenceStore:
    def __init__(self, store: Store, root: str | None = None, allowed_dirs: list[str] | None = None):
        self.store = store
        self.root = root or default_evidence_dir()
        self.allowed_dirs = [os.path.realpath(d) for d in (allowed_dirs or default_evidence_inbox_dirs())]

    def check_source(self, src_path: str) -> str:
        """D14: only regular, non-symlink, single-link files whose real path is inside an allowed inbox dir."""
        return self._check(src_path)[0]

    def _inside_allowed(self, real: str) -> bool:
        return any(real != a and os.path.commonpath([real, a]) == a for a in self.allowed_dirs)

    def _check(self, src_path: str) -> tuple[str, os.stat_result]:
        if not isinstance(src_path, str) or not src_path:
            raise EvidencePathError("path is required")
        abs_path = os.path.abspath(os.path.expanduser(src_path))
        try:
            st = os.lstat(abs_path)
        except FileNotFoundError:
            raise FileNotFoundError(src_path) from None
        if stat.S_ISLNK(st.st_mode):
            raise EvidencePathError(f"symlinks are not accepted as evidence: {src_path}")
        if not stat.S_ISREG(st.st_mode):
            raise EvidencePathError(f"not a regular file: {src_path}")
        if st.st_nlink != 1:
            raise EvidencePathError(f"hard-linked files are not accepted as evidence: {src_path}")
        real = os.path.realpath(abs_path)
        if not self._inside_allowed(real):
            raise EvidencePathError(f"evidence files must be inside {', '.join(self.allowed_dirs)}: {src_path}")
        return real, st

    def _open_checked(self, real: str, checked: os.stat_result) -> int:
        """Open without following symlinks and re-verify the opened file itself (closes the check/copy race)."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(real, flags)
        except OSError as exc:  # ELOOP when the path was swapped for a symlink
            raise EvidencePathError(f"could not open evidence file safely: {exc.strerror}") from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise EvidencePathError("opened evidence is not a regular file")
            if st.st_nlink != 1:
                raise EvidencePathError("hard-linked files are not accepted as evidence")
            if (st.st_dev, st.st_ino) != (checked.st_dev, checked.st_ino):
                raise EvidencePathError("evidence file changed between check and open")
            proc = f"/proc/self/fd/{fd}"
            if os.path.exists(proc) and not self._inside_allowed(os.path.realpath(os.readlink(proc))):
                raise EvidencePathError("opened evidence file is outside the evidence inbox")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _entity_dir(self, related_ref: str, evidence_id: str) -> str:
        entity = _SAFE.sub("_", related_ref.split("_", 1)[0] or "misc")
        return os.path.join(self.root, entity, _SAFE.sub("_", related_ref), evidence_id)

    def add_file(self, kind: str, src_path: str, related_ref: str, notes: str = "") -> dict[str, Any]:
        if kind not in FILE_KINDS:
            raise ValueError(f"{kind} is not a file evidence kind")
        real, checked = self._check(src_path)
        fd = self._open_checked(real, checked)
        evidence_id = new_id("evd")
        dest_dir = self._entity_dir(related_ref, evidence_id)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(real))
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb") as src:  # copy from the verified fd, never by path again
            out_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(out_fd, "wb") as out:
                for chunk in iter(lambda: src.read(65536), b""):
                    digest.update(chunk)
                    out.write(chunk)
        return self.store.insert("evidence", {
            "id": evidence_id, "kind": kind, "path_or_ref": dest, "sha256": digest.hexdigest(),
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
