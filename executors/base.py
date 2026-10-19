"""Gate-enforcing executor base class.

Concrete executors (browser playbooks, Bland calls, email handoff) arrive in M1+. Every executor
must subclass :class:`GuardedExecutor`, which refuses to do anything unless the confirmation gate
says the request is approved, and which records an ActionRun with evidence.
"""
from __future__ import annotations

from typing import Any

from core.action_gate import ActionGate


class ExecutionResult(dict):
    """{'outcome': success|partial|failed|needs_user, 'evidence_ids': [...], 'error_code': str|None, 'log': [...]}"""


class GuardedExecutor:
    executor_name = "manual"

    def __init__(self, gate: ActionGate):
        self.gate = gate

    def run(self, request_id: str) -> dict[str, Any]:
        run = self.gate.begin_execution(request_id, self.executor_name)  # raises NotApprovedError
        request = self.gate.get(request_id)
        try:
            result = self.perform(request, run)
        except Exception as exc:  # never leave a run open on a crash
            return self.gate.finish_execution(run["id"], "failed", error_code=type(exc).__name__,
                                              log=[{"error": str(exc)}])
        return self.gate.finish_execution(run["id"], result["outcome"], result.get("evidence_ids"),
                                          result.get("error_code"), result.get("log"))

    def perform(self, request: dict[str, Any], run: dict[str, Any]) -> ExecutionResult:
        raise NotImplementedError
