"""R3 reminder_dispatch (hourly): due reminders -> chat texts, held during quiet hours (22:00-08:00 by default)."""
from __future__ import annotations

from typing import Any

from core.action_gate import ActionGate
from core.store import Store
from modules.trials import Trials


def run(store: Store, gate: ActionGate, lang: str = "en") -> dict[str, Any]:
    return Trials(store, gate).dispatch_due(lang)
