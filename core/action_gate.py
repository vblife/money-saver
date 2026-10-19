"""Confirmation gate (spec FR-0-5, 6.9, and chapter 8).

Every consequential action is an ``ActionRequest``. Nothing may execute unless the request
was explicitly approved by the user for that exact request. State machine::

    pending_user --confirm--> approved --begin/finish--> executed | failed
         |  \\--reject--> rejected
         |   \\--expire--> expired
         \\--cancel_by_user--> cancelled_by_user   (also allowed from approved, before execution)
    approved --amend (material change)--> pending_user   (must be re-confirmed, 8.2)

Executors must call :meth:`ActionGate.begin_execution`, which raises :class:`NotApprovedError`
for anything that is not approved. Terminal states never transition again.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

from . import privacy
from .store import Store
from .util import parse_iso, to_iso, utcnow

ACTION_TYPES = ("cancel_subscription", "negotiate_bill", "place_call", "submit_claim", "send_email",
                "rebook_travel", "submit_form_with_pii", "purchase")
DISABLED_ACTION_TYPES = {"purchase": "Purchases are disabled in the MVP (spec 8.1 #4, chapter 11); share a link instead."}
NEEDS_DRAFT = {"send_email": "email draft", "place_call": "call script", "negotiate_bill": "call script"}
EXECUTORS = ("browser", "bland_call", "email", "manual")
RUN_OUTCOMES = ("success", "partial", "failed", "needs_user")
TERMINAL = {"rejected", "expired", "executed", "failed", "cancelled_by_user"}
DEFAULT_TTL = timedelta(hours=72)
TRANSITIONS = {
    "pending_user": {"approved", "rejected", "expired", "cancelled_by_user"},
    "approved": {"executed", "failed", "cancelled_by_user", "pending_user"},
}


class GateError(Exception):
    """Base error for the confirmation gate."""


class NotApprovedError(GateError):
    """Raised when something tries to execute an action that is not approved."""


class InvalidTransitionError(GateError):
    pass


class ActionNotAllowedError(GateError):
    pass


class NotExplicitConfirmationError(GateError):
    pass


# --- explicit confirmation (8.2) -----------------------------------------------------------
_AFFIRM = re.compile(
    r"\b(confirm(ed)?|approve(d)?|yes|go ahead|do it|proceed|authori[sz]e(d)?)\b|确认|同意|批准|可以执行|执行吧|没问题，?执行",
    re.I)
_VAGUE = re.compile(
    r"\b(maybe|perhaps|i guess|guess so|probably|might|whatever|not sure|i think so|kinda|sort of|if you want|up to you)\b"
    r"|好像|随便|也许|可能|大概|应该可以吧|看着办|你决定|无所谓|再说",
    re.I)
_NEGATE = re.compile(
    r"\b(no|not|don'?t|do not|never|stop|wait|hold on|cancel that|nope)\b|不要|不用|别|先不|暂不|不确认|不同意|停",
    re.I)


def is_explicit_confirmation(message: str | None) -> bool:
    """True only for an unambiguous affirmative. Vague replies, negations, questions, and
    silence (empty) are NOT confirmation."""
    if not message or not message.strip():
        return False
    text = message.strip()
    if text.endswith(("?", "？")):
        return False
    if _VAGUE.search(text) or _NEGATE.search(text):
        return False
    return bool(_AFFIRM.search(text))


class ActionGate:
    def __init__(self, store: Store, now_fn: Callable[[], datetime] = utcnow):
        self.store = store
        self.now_fn = now_fn

    # -- helpers ----------------------------------------------------------------------
    def _now(self) -> str:
        return to_iso(self.now_fn())

    def get(self, request_id: str) -> dict[str, Any]:
        req = self.store.get("action_requests", request_id)
        if req is None:
            raise KeyError(f"action request {request_id} not found")
        return req

    def _transition(self, req: dict[str, Any], to: str, note: str, **changes: Any) -> dict[str, Any]:
        frm = req["status"]
        if to not in TRANSITIONS.get(frm, set()):
            raise InvalidTransitionError(f"cannot move action request {req['id']} from {frm} to {to}")
        history = list(req.get("history") or [])
        history.append({"ts": self._now(), "from": frm, "to": to, "note": note})
        return self.store.update("action_requests", req["id"], {"status": to, "history": history, **changes})

    # -- create -------------------------------------------------------------------------
    def create(self, action_type: str, target_ref: str, summary: str, plan_steps: list[str],
               disclosed_fields: list[dict[str, str]] | None = None, constraints: dict | None = None,
               draft_content: str | None = None, expected_saving_cents: int | None = None,
               risk_notes: str = "", expires_at: datetime | None = None,
               execute_not_before: datetime | None = None) -> dict[str, Any]:
        if action_type not in ACTION_TYPES:
            raise ActionNotAllowedError(f"unknown action_type: {action_type}")
        if action_type in DISABLED_ACTION_TYPES:
            raise ActionNotAllowedError(DISABLED_ACTION_TYPES[action_type])
        if not summary or not summary.strip():
            raise GateError("summary is required")
        if not plan_steps:
            raise GateError("plan_steps must list the concrete steps that will be executed")
        if action_type in NEEDS_DRAFT and not (draft_content and draft_content.strip()):
            raise GateError(f"{action_type} requires a {NEEDS_DRAFT[action_type]} in draft_content")
        disclosed_fields = list(disclosed_fields or [])
        approved = set(self.store.get_user().get("approved_pii_fields") or [])
        for item in disclosed_fields:
            field = item.get("field")
            if field not in approved:
                raise GateError(f"field '{field}' is not in approved_pii_fields (PV-5); ask the user first")
            privacy.assert_no_forbidden_secrets(str(item.get("value_masked", "")), field=f"disclosed {field}")
        if action_type == "submit_form_with_pii" and not disclosed_fields:
            raise GateError("submit_form_with_pii must list every field that will be submitted")
        now = self.now_fn()
        expires = expires_at or (now + DEFAULT_TTL)
        if expires <= now:
            raise GateError("expires_at must be in the future")
        row = {
            "action_type": action_type, "target_ref": target_ref, "summary": summary.strip(),
            "plan_steps": list(plan_steps), "disclosed_fields": disclosed_fields, "constraints": constraints or {},
            "draft_content": draft_content, "expected_saving_cents": expected_saving_cents, "risk_notes": risk_notes,
            "status": "pending_user", "expires_at": to_iso(expires),
            "execute_not_before": to_iso(execute_not_before) if execute_not_before else None,
            "history": [{"ts": to_iso(now), "from": None, "to": "pending_user", "note": "created"}],
        }
        return self.store.insert("action_requests", row)

    # -- user decisions -------------------------------------------------------------------
    def _check_live(self, req: dict[str, Any]) -> None:
        if req["status"] == "pending_user" and parse_iso(req["expires_at"]) <= self.now_fn():
            self._transition(req, "expired", "expired before confirmation")
            raise InvalidTransitionError(f"action request {req['id']} has expired; create a new one")

    def confirm(self, request_id: str, user_message: str) -> dict[str, Any]:
        """Approve one specific request with the user's verbatim message."""
        req = self.get(request_id)
        if req["status"] != "pending_user":
            raise InvalidTransitionError(f"action request {request_id} is {req['status']}, not pending_user")
        self._check_live(req)
        if not is_explicit_confirmation(user_message):
            raise NotExplicitConfirmationError(
                "the reply is not an explicit confirmation (vague, negative, a question, or empty); ask again")
        return self._transition(req, "approved", "confirmed by user", approved_at=self._now(),
                                approved_by_message=user_message.strip())

    def confirm_batch(self, request_ids: list[str], user_message: str) -> list[dict[str, Any]]:
        """Approve a listed batch (8.2): all-or-nothing; every listed request must be pending."""
        if not request_ids or len(set(request_ids)) != len(request_ids):
            raise GateError("batch confirmation needs a non-empty list of distinct request ids")
        if not is_explicit_confirmation(user_message):
            raise NotExplicitConfirmationError("the reply is not an explicit confirmation; ask again")
        reqs = [self.get(rid) for rid in request_ids]
        for req in reqs:
            if req["status"] != "pending_user":
                raise InvalidTransitionError(f"action request {req['id']} is {req['status']}, not pending_user")
            self._check_live(req)
        note = f"batch[{', '.join(request_ids)}]: {user_message.strip()}"
        return [self._transition(r, "approved", "confirmed by user (batch)", approved_at=self._now(),
                                 approved_by_message=note) for r in reqs]

    def reject(self, request_id: str, note: str = "rejected by user") -> dict[str, Any]:
        return self._transition(self.get(request_id), "rejected", note)

    def cancel_by_user(self, request_id: str, note: str = "revoked by user") -> dict[str, Any]:
        """Revoke a pending or approved-but-not-started request (pre-authorization revoke window)."""
        req = self.get(request_id)
        if self.store.find("action_runs", {"action_request_id": request_id}):
            raise InvalidTransitionError("execution already started; it can no longer be revoked here")
        return self._transition(req, "cancelled_by_user", note)

    def amend(self, request_id: str, changes: dict[str, Any], reason: str) -> dict[str, Any]:
        """Material plan change (8.2): any approval is cleared and the user must confirm again."""
        allowed = {"summary", "plan_steps", "disclosed_fields", "constraints", "draft_content",
                   "expected_saving_cents", "risk_notes"}
        bad = set(changes) - allowed
        if bad:
            raise GateError(f"cannot amend fields: {sorted(bad)}")
        req = self.get(request_id)
        if self.store.find("action_runs", {"action_request_id": request_id}):
            raise InvalidTransitionError("execution already started; stop and create a new request")
        if req["status"] not in ("pending_user", "approved"):
            raise InvalidTransitionError(f"cannot amend a {req['status']} request")
        fresh_expiry = to_iso(self.now_fn() + DEFAULT_TTL)
        if req["status"] == "approved":
            return self._transition(req, "pending_user", f"amended, needs re-confirmation: {reason}",
                                    approved_at=None, approved_by_message=None, expires_at=fresh_expiry, **changes)
        history = list(req["history"]) + [{"ts": self._now(), "from": "pending_user", "to": "pending_user",
                                           "note": f"amended: {reason}"}]
        return self.store.update("action_requests", request_id, {**changes, "history": history})

    def expire_due(self) -> list[str]:
        """R10: mark pending requests past expires_at as expired. Idempotent."""
        now = self.now_fn()
        expired = []
        for req in self.store.find("action_requests", {"status": "pending_user"}):
            if parse_iso(req["expires_at"]) <= now:
                self._transition(req, "expired", "expired before confirmation")
                expired.append(req["id"])
        return expired

    # -- execution ------------------------------------------------------------------------
    def require_approved(self, request_id: str) -> dict[str, Any]:
        req = self.store.get("action_requests", request_id)
        if req is None:
            raise NotApprovedError(f"no action request {request_id}; nothing may execute without one")
        if req["status"] != "approved" or not req.get("approved_by_message"):
            raise NotApprovedError(f"action request {request_id} is {req['status']}; explicit user approval required")
        if req["action_type"] in DISABLED_ACTION_TYPES:
            raise NotApprovedError(DISABLED_ACTION_TYPES[req["action_type"]])
        nb = req.get("execute_not_before")
        if nb and parse_iso(nb) > self.now_fn():
            raise NotApprovedError(f"action request {request_id} is inside its revoke window until {nb}")
        return req

    def begin_execution(self, request_id: str, executor: str) -> dict[str, Any]:
        if executor not in EXECUTORS:
            raise GateError(f"unknown executor: {executor}")
        self.require_approved(request_id)
        existing = self.store.find("action_runs", {"action_request_id": request_id})
        if existing:
            run = existing[0]
            if run["outcome"] == "needs_user" and run["finished_at"] is None:
                return self.store.update("action_runs", run["id"], {"outcome": None})  # resume
            raise InvalidTransitionError(f"action request {request_id} already has a run")
        return self.store.insert("action_runs", {"action_request_id": request_id, "started_at": self._now(),
                                                 "executor": executor, "log": []})

    def finish_execution(self, run_id: str, outcome: str, evidence_ids: list[str] | None = None,
                         error_code: str | None = None, log: list | None = None) -> dict[str, Any]:
        if outcome not in RUN_OUTCOMES:
            raise GateError(f"unknown outcome: {outcome}")
        run = self.store.get("action_runs", run_id)
        if run is None:
            raise KeyError(run_id)
        req = self.require_approved(run["action_request_id"])
        merged_log = list(run["log"]) + list(log or [])
        evidence = list(run["evidence_ids"]) + list(evidence_ids or [])
        if outcome in ("success", "partial") and not evidence:
            raise GateError("FR-0-6: a successful run must attach at least one Evidence record")
        if outcome == "needs_user":
            return self.store.update("action_runs", run_id, {"outcome": outcome, "log": merged_log,
                                                             "evidence_ids": evidence, "error_code": error_code})
        run = self.store.update("action_runs", run_id, {"outcome": outcome, "finished_at": self._now(),
                                                        "log": merged_log, "evidence_ids": evidence,
                                                        "error_code": error_code})
        self._transition(req, "executed" if outcome in ("success", "partial") else "failed",
                         f"run {run_id} finished: {outcome}")
        return run

    # -- audit (8.4) ----------------------------------------------------------------------
    def timeline(self) -> list[dict[str, Any]]:
        """'What have you done for me?' All requests with their run, newest first."""
        out = []
        for req in self.store.find("action_requests", order_by="created_at DESC"):
            runs = self.store.find("action_runs", {"action_request_id": req["id"]})
            out.append({"request": req, "run": runs[0] if runs else None})
        return out
