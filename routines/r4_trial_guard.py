"""R4 trial_guard (daily 09:00): T-2/T-1 reminders, the 2-hour warning before a pre-authorized cancellation, and the
list of approved cancellations whose undo window has passed. It never executes them (M1d does, through the gate)."""
from __future__ import annotations

from typing import Any

from core.action_gate import ActionGate
from core.store import Store
from modules.trials import Trials


def run(store: Store, gate: ActionGate, lang: str = "en") -> dict[str, Any]:
    trials = Trials(store, gate)
    out = trials.trial_guard(lang)
    out["reminders_due"] = trials.dispatch_due(lang)
    return out
