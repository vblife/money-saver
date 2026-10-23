"""Email cancellation handoff (FR-1-12).

This executor never sends email. After the gate approves the request and the live guard allows the email channel,
it returns the exact approved email (recipient from the reviewed registry, subject, and body) for the host's own
approval flow: the bot creates a draft in the user's mail app, and the user reviews and sends it themselves. The run
stays ``needs_user`` until the user forwards the merchant's confirmation reply, which becomes the evidence.
"""
from __future__ import annotations

from typing import Any

from executors.base import ExecutionResult, GuardedExecutor

SUBJECT = {"en": "Cancel my {name} subscription", "zh": "取消 {name} 订阅"}
GENERIC_SUBJECT = {"en": "Cancel my subscription", "zh": "取消订阅"}


class EmailHandoffExecutor(GuardedExecutor):
    executor_name = "email"

    def __init__(self, gate: Any, lang: str = "en"):
        super().__init__(gate)
        self.lang = lang
        self.handoff: dict[str, Any] | None = None

    def perform(self, request: dict[str, Any], run: dict[str, Any]) -> ExecutionResult:
        c = request.get("constraints") or {}
        to, body = c.get("cancel_target"), request.get("draft_content")
        if not to or not body:
            return ExecutionResult(outcome="failed", error_code="no_email_draft", evidence_ids=[],
                                   log=[{"error": "the request has no reviewed cancel address or no approved text"}])
        sub = self.gate.store.get("subscriptions", c.get("subscription_id") or request["target_ref"]) or {}
        lang = self.lang if self.lang in SUBJECT else "en"
        name = sub.get("display_name")
        subject = SUBJECT[lang].format(name=name) if name else GENERIC_SUBJECT[lang]
        self.handoff = {"to": to, "subject": subject, "body": body, "sent": False,
                        "instructions": "Create a draft with exactly this recipient, subject and body in the user's "
                                        "own mail app for them to review and send. Do not send it yourself. Ask the "
                                        "user to forward the merchant's confirmation reply."}
        return ExecutionResult(outcome="needs_user", error_code="awaiting_user_send", evidence_ids=[],
                               log=[{"email_handoff": {k: v for k, v in self.handoff.items() if k != "instructions"}}])
