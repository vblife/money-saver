"""R10 action_request_expiry (hourly): expire stale requests; one last reminder for a trial cancel request that
expired before the trial ended (10.2)."""
from __future__ import annotations

from typing import Any

from core.action_gate import ActionGate
from core.store import Store
from modules.trials import Trials


def run(store: Store, gate: ActionGate, lang: str = "en") -> dict[str, Any]:
    return Trials(store, gate).expiry(lang)
