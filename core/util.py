"""Small shared helpers: ids, UTC time, paths."""
from __future__ import annotations

import os
import secrets
import string
from datetime import date, datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")

# Spec section 6: data lives in the bot's persistent directory. Overridable for tests/installs.
DEFAULT_HOME = "/workspace/money-saver"


def data_home() -> str:
    return os.environ.get("MSB_HOME", DEFAULT_HOME)


def default_db_path() -> str:
    return os.environ.get("MSB_DB", os.path.join(data_home(), "data", "msb.db"))


def default_evidence_dir() -> str:
    return os.environ.get("MSB_EVIDENCE_DIR", os.path.join(data_home(), "evidence"))


def default_evidence_inbox_dirs() -> list[str]:
    """Directories record_evidence may copy files from (D14). MSB_EVIDENCE_INBOX may list several (os.pathsep)."""
    env = os.environ.get("MSB_EVIDENCE_INBOX")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    return [os.path.join(data_home(), "inbox")]


def new_id(prefix: str) -> str:
    # Letters only: ids never contain digit runs that could look like card numbers to the privacy scanner.
    return f"{prefix}_{''.join(secrets.choice(string.ascii_lowercase) for _ in range(16))}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; use UTC-aware datetimes")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])
