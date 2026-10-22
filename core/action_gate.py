"""Confirmation gate (spec FR-0-5, 6.9, and chapter 8).

Every consequential action is an ``ActionRequest``. Nothing may execute unless the request
was explicitly approved by the user for that exact request. State machine::

    pending_user --confirm--> approved --begin/finish--> executed | failed
         |  \\--reject--> rejected
         |   \\--expire--> expired
         \\--cancel_by_user--> cancelled_by_user   (also allowed from approved, before execution)
    approved --amend (material change)--> pending_user   (must be re-confirmed, 8.2)
    approved --execution deadline passed--> expired

Binding (see docs/decisions.md D11): each request renders one confirmation text and a short
confirmation code. Approval needs an exact affirmative reply that either quotes the code or is
accompanied by the SHA-256 of the exact text that was shown. Any amendment re-renders both, so
an old approval can never cover a changed plan. Each request is bound to one executor channel,
and an approval expires if it is not executed in time.

Executors must call :meth:`ActionGate.begin_execution`, which raises :class:`NotApprovedError`
for anything that is not approved. Terminal states never transition again.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Callable

from . import notifier, privacy
from .store import Store
from .util import new_id, parse_iso, to_iso, utcnow

ACTION_TYPES = ("cancel_subscription", "negotiate_bill", "place_call", "submit_claim", "send_email",
                "rebook_travel", "submit_form_with_pii", "purchase")
DISABLED_ACTION_TYPES = {"purchase": "Purchases are disabled in the MVP (spec 8.1 #4, chapter 11); share a link instead."}
EXECUTORS = ("browser", "bland_call", "email", "manual")
# action_type -> allowed executor channels; the first one is the default.
ALLOWED_EXECUTORS = {
    "cancel_subscription": ("browser", "bland_call", "email", "manual"),
    "negotiate_bill": ("bland_call",),
    "place_call": ("bland_call",),
    "submit_claim": ("browser", "email", "bland_call", "manual"),
    "send_email": ("email",),
    "rebook_travel": ("browser",),
    "submit_form_with_pii": ("browser",),
}
NEEDS_DRAFT_BY_EXECUTOR = {"email": "email draft", "bland_call": "call script"}
RUN_OUTCOMES = ("success", "partial", "failed", "needs_user")
TERMINAL = {"rejected", "expired", "executed", "failed", "cancelled_by_user"}
DEFAULT_TTL = timedelta(hours=72)            # 6.9: unconfirmed requests expire after 72 hours
DEFAULT_APPROVAL_TTL = timedelta(hours=24)   # D12: an approval must be acted on within 24 hours
AMENDABLE = {"summary", "plan_steps", "disclosed_fields", "constraints", "draft_content", "expected_saving_cents",
             "risk_notes", "executor"}
TRANSITIONS = {
    "pending_user": {"approved", "rejected", "expired", "cancelled_by_user"},
    "approved": {"executed", "failed", "cancelled_by_user", "pending_user", "expired"},
}
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"  # letters only, no I/L/O


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


# --- explicit confirmation (8.2): fail closed --------------------------------------------------------
# The whole reply (after normalization, and after removing the confirmation code) must be one of these.
AFFIRMATIVE_REPLIES = frozenset({
    "confirm", "confirmed", "i confirm", "yes confirm", "yes i confirm", "confirm it", "yes confirm it",
    "approve", "approved", "i approve", "yes approve", "yes i approve",
    "confirm all", "yes confirm all", "i confirm all", "approve all", "i approve all",
    "确认", "我确认", "确认执行", "确认批准", "同意", "我同意", "批准", "我批准", "是的确认", "好的确认",
    "确认全部", "全部确认", "全部批准",
})
_STRIP_CHARS = ".!。！~～ \t\n\r"
_SEPARATORS = re.compile(r"[\s,，、:：;；\-–—\"'“”‘’「」『』()（）\[\]【】]+")


def _normalize(message: str) -> str:
    text = unicodedata.normalize("NFKC", message).strip().lower()
    text = _SEPARATORS.sub(" ", text).strip(_STRIP_CHARS)
    return re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", text).strip()


def is_explicit_confirmation(message: str | None, codes: list[str] | tuple[str, ...] = ()) -> bool:
    """True only if the whole reply is an allow-listed affirmative, optionally followed or preceded
    by the request's confirmation code(s). Anything else - negations, questions, conditionals,
    extra words, empty replies - is not a confirmation."""
    if not message or not isinstance(message, str) or not message.strip():
        return False
    if "?" in message or "？" in message:
        return False
    text = _normalize(message)
    if len(text) > 80:
        return False
    for code in codes:
        if code:
            text = re.sub(rf"(?<![a-z0-9]){re.escape(code.lower())}(?![a-z0-9])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", text)
    return text in AFFIRMATIVE_REPLIES


def _mentions_all_codes(message: str, codes: list[str]) -> bool:
    text = _normalize(message)
    return all(re.search(rf"(?<![a-z0-9]){re.escape(c.lower())}(?![a-z0-9])", text) for c in codes)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_confirmation_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


class ActionGate:
    def __init__(self, store: Store, now_fn: Callable[[], datetime] = utcnow,
                 approval_ttl: timedelta | None = None):
        self.store = store
        self.now_fn = now_fn
        self._approval_ttl = approval_ttl

    # -- helpers ----------------------------------------------------------------------
    def _now(self) -> str:
        return to_iso(self.now_fn())

    @property
    def approval_ttl(self) -> timedelta:
        if self._approval_ttl is not None:
            return self._approval_ttl
        hours = (self.store.get_user().get("thresholds") or {}).get("approval_ttl_hours")
        return timedelta(hours=float(hours)) if hours else DEFAULT_APPROVAL_TTL

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

    # -- validation shared by create() and amend() --------------------------------------
    def _validate(self, row: dict[str, Any]) -> None:
        action_type = row["action_type"]
        if action_type not in ACTION_TYPES:
            raise ActionNotAllowedError(f"unknown action_type: {action_type}")
        if action_type in DISABLED_ACTION_TYPES:
            raise ActionNotAllowedError(DISABLED_ACTION_TYPES[action_type])
        executor = row.get("executor")
        if executor not in ALLOWED_EXECUTORS[action_type]:
            raise GateError(f"executor '{executor}' is not allowed for {action_type}; "
                            f"allowed: {', '.join(ALLOWED_EXECUTORS[action_type])}")
        summary = row.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise GateError("summary is required")
        steps = row.get("plan_steps")
        if not isinstance(steps, list) or not steps or not all(isinstance(s, str) and s.strip() for s in steps):
            raise GateError("plan_steps must list the concrete steps that will be executed")
        draft = row.get("draft_content")
        if executor in NEEDS_DRAFT_BY_EXECUTOR and not (isinstance(draft, str) and draft.strip()):
            raise GateError(f"{action_type} via {executor} requires a {NEEDS_DRAFT_BY_EXECUTOR[executor]} in draft_content")
        disclosed = row.get("disclosed_fields") or []
        if not isinstance(disclosed, list):
            raise GateError("disclosed_fields must be a list")
        approved = set(self.store.get_user().get("approved_pii_fields") or [])
        for item in disclosed:
            if not isinstance(item, dict):
                raise GateError("each disclosed field must be {field, value_masked}")
            field = item.get("field")
            if field not in approved:
                raise GateError(f"field '{field}' is not in approved_pii_fields (PV-5); ask the user first")
            privacy.assert_no_forbidden_secrets(str(item.get("value_masked", "")), field=f"disclosed {field}")
        if action_type == "submit_form_with_pii" and not disclosed:
            raise GateError("submit_form_with_pii must list every field that will be submitted")
        constraints = row.get("constraints")
        if constraints is not None and not isinstance(constraints, dict):
            raise GateError("constraints must be an object")
        saving = row.get("expected_saving_cents")
        if saving is not None and (not isinstance(saving, int) or isinstance(saving, bool)):
            raise GateError("expected_saving_cents must be an integer number of cents")
        for key in ("summary", "draft_content", "risk_notes"):
            if isinstance(row.get(key), str):
                privacy.assert_no_forbidden_secrets(row[key], field=key)
        for step in steps:
            privacy.assert_no_forbidden_secrets(step, field="plan_steps")

    def _render(self, row: dict[str, Any], lang: str) -> dict[str, Any]:
        """Fresh confirmation code + the exact text shown to the user + its digest."""
        code = new_confirmation_code()
        text = notifier.format_action_request({**row, "confirmation_code": code}, lang)
        return {"confirmation_code": code, "confirmation_text": text, "confirmation_sha256": sha256_text(text)}

    # -- create -------------------------------------------------------------------------
    def create(self, action_type: str, target_ref: str, summary: str, plan_steps: list[str],
               disclosed_fields: list[dict[str, str]] | None = None, constraints: dict | None = None,
               draft_content: str | None = None, expected_saving_cents: int | None = None,
               risk_notes: str = "", expires_at: datetime | None = None,
               execute_not_before: datetime | None = None, executor: str | None = None,
               lang: str = "en") -> dict[str, Any]:
        if action_type not in ACTION_TYPES:
            raise ActionNotAllowedError(f"unknown action_type: {action_type}")
        if action_type in DISABLED_ACTION_TYPES:
            raise ActionNotAllowedError(DISABLED_ACTION_TYPES[action_type])
        now = self.now_fn()
        expires = expires_at or (now + DEFAULT_TTL)
        if expires <= now:
            raise GateError("expires_at must be in the future")
        row = {
            "id": new_id("ar"), "action_type": action_type, "target_ref": target_ref,
            "summary": summary.strip() if isinstance(summary, str) else summary,
            "plan_steps": list(plan_steps or []), "disclosed_fields": list(disclosed_fields or []),
            "constraints": constraints or {}, "draft_content": draft_content,
            "expected_saving_cents": expected_saving_cents, "risk_notes": risk_notes or "",
            "executor": executor or ALLOWED_EXECUTORS[action_type][0],
            "status": "pending_user", "expires_at": to_iso(expires),
            "execute_not_before": to_iso(execute_not_before) if execute_not_before else None,
            "history": [{"ts": to_iso(now), "from": None, "to": "pending_user", "note": "created"}],
        }
        self._validate(row)
        row.update(self._render(row, lang))
        return self.store.insert("action_requests", row)

    # -- user decisions -------------------------------------------------------------------
    def _check_live(self, req: dict[str, Any]) -> None:
        if req["status"] == "pending_user" and parse_iso(req["expires_at"]) <= self.now_fn():
            self._transition(req, "expired", "expired before confirmation")
            raise InvalidTransitionError(f"action request {req['id']} has expired; create a new one")

    def _execute_by(self, req: dict[str, Any]) -> str:
        start = self.now_fn()
        nb = req.get("execute_not_before")
        if nb and parse_iso(nb) > start:
            start = parse_iso(nb)
        return to_iso(start + self.approval_ttl)

    def confirm(self, request_id: str, user_message: str, shown_text_sha256: str | None = None,
                source: str | None = None) -> dict[str, Any]:
        """Approve one specific request with the user's verbatim reply.

        The reply must be an exact affirmative (see AFFIRMATIVE_REPLIES). It must also be bound to
        what the user saw: either the reply quotes the request's confirmation code, or
        ``shown_text_sha256`` equals the digest of the confirmation text that was displayed."""
        req = self.get(request_id)
        if req["status"] != "pending_user":
            raise InvalidTransitionError(f"action request {request_id} is {req['status']}, not pending_user")
        self._check_live(req)
        code = req.get("confirmation_code")
        if not is_explicit_confirmation(user_message, [code] if code else []):
            raise NotExplicitConfirmationError(
                "the reply is not an explicit confirmation (it must be exactly e.g. 'confirm "
                f"{code}' / '确认 {code}', with no other words, negation, condition, or question); ask again")
        quoted = bool(code) and _mentions_all_codes(user_message, [code])
        if not quoted and shown_text_sha256 != req.get("confirmation_sha256"):
            raise NotExplicitConfirmationError(
                "approval is not bound to the confirmation text: the reply must quote the confirmation code, "
                "or shown_text_sha256 must match the text the user was shown (it changes after any amendment)")
        return self._transition(req, "approved", "confirmed by user", approved_at=self._now(),
                                approved_by_message=user_message.strip(), approval_source=source,
                                execute_by=self._execute_by(req))

    def batch_text(self, request_ids: list[str], lang: str = "en") -> str:
        return notifier.format_batch([self.get(rid) for rid in request_ids], lang)

    def confirm_batch(self, request_ids: list[str], user_message: str, shown_text_sha256: str | None = None,
                      source: str | None = None, lang: str = "en") -> list[dict[str, Any]]:
        """Approve a listed batch (8.2): all-or-nothing; every listed request must be pending, and the reply
        must quote every code or come with the digest of the exact batch text that listed them."""
        if not request_ids or len(set(request_ids)) != len(request_ids):
            raise GateError("batch confirmation needs a non-empty list of distinct request ids")
        reqs = [self.get(rid) for rid in request_ids]
        for req in reqs:
            if req["status"] != "pending_user":
                raise InvalidTransitionError(f"action request {req['id']} is {req['status']}, not pending_user")
            self._check_live(req)
        codes = [r.get("confirmation_code") or "" for r in reqs]
        if not is_explicit_confirmation(user_message, codes):
            raise NotExplicitConfirmationError("the reply is not an explicit confirmation; ask again")
        quoted = all(codes) and _mentions_all_codes(user_message, codes)
        if not quoted and shown_text_sha256 != sha256_text(notifier.format_batch(reqs, lang)):
            raise NotExplicitConfirmationError("approval is not bound to the batch text the user was shown")
        note = f"batch[{', '.join(request_ids)}]: {user_message.strip()}"
        return [self._transition(r, "approved", "confirmed by user (batch)", approved_at=self._now(),
                                 approved_by_message=note, approval_source=source,
                                 execute_by=self._execute_by(r)) for r in reqs]

    def reject(self, request_id: str, note: str = "rejected by user") -> dict[str, Any]:
        return self._transition(self.get(request_id), "rejected", note)

    def cancel_by_user(self, request_id: str, note: str = "revoked by user") -> dict[str, Any]:
        """Revoke a pending or approved-but-not-started request (pre-authorization revoke window)."""
        req = self.get(request_id)
        if self.store.find("action_runs", {"action_request_id": request_id}):
            raise InvalidTransitionError("execution already started; it can no longer be revoked here")
        return self._transition(req, "cancelled_by_user", note)

    def amend(self, request_id: str, changes: dict[str, Any], reason: str, lang: str = "en") -> dict[str, Any]:
        """Material plan change (8.2). The amended request is validated exactly like a new one, gets a new
        confirmation code and text, and any approval is cleared: the user must confirm again."""
        bad = set(changes) - AMENDABLE
        if bad:
            raise GateError(f"cannot amend fields: {sorted(bad)}")
        if not changes:
            raise GateError("nothing to amend")
        req = self.get(request_id)
        if self.store.find("action_runs", {"action_request_id": request_id}):
            raise InvalidTransitionError("execution already started; stop and create a new request")
        if req["status"] not in ("pending_user", "approved"):
            raise InvalidTransitionError(f"cannot amend a {req['status']} request")
        merged = {**req, **changes}
        self._validate(merged)
        rendered = self._render(merged, lang)
        fresh_expiry = to_iso(self.now_fn() + DEFAULT_TTL)
        reset = {"approved_at": None, "approved_by_message": None, "approval_source": None, "execute_by": None,
                 "expires_at": fresh_expiry, **rendered, **changes}
        if req["status"] == "approved":
            return self._transition(req, "pending_user", f"amended, needs re-confirmation: {reason}", **reset)
        history = list(req["history"]) + [{"ts": self._now(), "from": "pending_user", "to": "pending_user",
                                           "note": f"amended: {reason}"}]
        return self.store.update("action_requests", request_id, {**reset, "history": history})

    def expire_due(self) -> list[str]:
        """R10: expire pending requests past expires_at and approvals past their execution deadline. Idempotent."""
        now = self.now_fn()
        expired = []
        for req in self.store.find("action_requests", {"status": "pending_user"}):
            if parse_iso(req["expires_at"]) <= now:
                self._transition(req, "expired", "expired before confirmation")
                expired.append(req["id"])
        for req in self.store.find("action_requests", {"status": "approved"}):
            if self._approval_lapsed(req, now) and not self.store.find("action_runs", {"action_request_id": req["id"]}):
                self._transition(req, "expired", "approval expired before execution")
                expired.append(req["id"])
        return expired

    @staticmethod
    def _approval_lapsed(req: dict[str, Any], now: datetime) -> bool:
        return not req.get("execute_by") or parse_iso(req["execute_by"]) <= now

    # -- execution ------------------------------------------------------------------------
    def require_approved(self, request_id: str, executor: str | None = None) -> dict[str, Any]:
        """Gate check before starting (or resuming) execution."""
        req = self.store.get("action_requests", request_id)
        if req is None:
            raise NotApprovedError(f"no action request {request_id}; nothing may execute without one")
        if req["status"] != "approved" or not req.get("approved_by_message"):
            raise NotApprovedError(f"action request {request_id} is {req['status']}; explicit user approval required")
        if req["action_type"] in DISABLED_ACTION_TYPES or req["action_type"] not in ALLOWED_EXECUTORS:
            raise NotApprovedError(DISABLED_ACTION_TYPES.get(req["action_type"], "action type not allowed"))
        if executor is not None and executor != req["executor"]:
            raise NotApprovedError(f"action request {request_id} was approved for '{req['executor']}', not "
                                   f"'{executor}'; amend it and get confirmation again to change channel")
        now = self.now_fn()
        nb = req.get("execute_not_before")
        if nb and parse_iso(nb) > now:
            raise NotApprovedError(f"action request {request_id} is inside its revoke window until {nb}")
        if self._approval_lapsed(req, now):
            if not self.store.find("action_runs", {"action_request_id": request_id}):
                self._transition(req, "expired", "approval expired before execution")
            raise NotApprovedError(f"the approval for {request_id} has expired; ask the user again")
        return req

    def begin_execution(self, request_id: str, executor: str) -> dict[str, Any]:
        if executor not in EXECUTORS:
            raise GateError(f"unknown executor: {executor}")
        self.require_approved(request_id, executor)
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
        if run["finished_at"] is not None:
            raise InvalidTransitionError(f"run {run_id} is already finished")
        req = self.get(run["action_request_id"])
        if req["status"] != "approved":  # a started run may finish after the approval deadline
            raise NotApprovedError(f"action request {req['id']} is {req['status']}")
        merged_log = list(run["log"]) + list(log or [])
        evidence = list(run["evidence_ids"]) + list(evidence_ids or [])
        if outcome in ("success", "partial") and not evidence:
            raise GateError("FR-0-6: a successful run must attach at least one Evidence record")
        for eid in evidence:
            if self.store.get("evidence", eid) is None:
                raise GateError(f"unknown evidence id {eid}")
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
