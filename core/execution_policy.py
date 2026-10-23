"""Live-execution safety gates for M1d (D33-D36).

The confirmation gate (``core/action_gate.py``) decides whether the *user* approved one exact request. This module adds
the operator-level gates that sit on top of it for every channel that acts in the outside world (browser, Bland call,
email). Each one fails closed:

* **Live switch (D33).** ``config/execution.json`` has one boolean per channel, all ``false`` in the repo. While a
  channel is off, ``start_action_run`` / ``ActionGate.begin_execution`` refuse to start a run on it, whatever the user
  approved; the bot can only dry-run or hand the user the manual guide. Unknown keys, non-boolean values, or an
  unreadable file turn every channel off.
* **First live cancellation per merchant (D34).** Until the bot has cancelled a merchant for real once (a live run with
  outcome ``success``), a cancel request for that merchant must carry ``constraints.first_live_cancel_merchant`` set to
  the merchant id. The gate adds it on create and amend, and the confirmation card shows it, so the user's approval
  covers it explicitly.
* **First paid Bland call (D35).** No live ``bland_call`` run may start before the first one is approved by Charles,
  recorded as ``bland_first_paid_call_approval`` in ``config/execution.json`` (a [RISKY] PR). After one live call has
  been placed, later calls need only the user's per-call approval.
* **Reviewed targets only (D32/D36).** A live cancellation may only use a cancel URL, phone number, or email address
  from the reviewed merchant registry (``config/merchant_registry.json``), for a merchant marked ``verified``, and the
  request must name that exact target in ``constraints.cancel_target``. Proposals the bot recorded itself
  (``merchant_method_proposals``, Q11) are never read here.

Nothing in this module talks to the network.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

from .action_gate import GateError
from .util import CONFIG_DIR, to_iso, utcnow

CONFIG_PATH = os.path.join(CONFIG_DIR, "execution.json")
REGISTRY_PATH = os.path.join(CONFIG_DIR, "merchant_registry.json")
LIVE_EXECUTORS = ("browser", "bland_call", "email")
CONFIG_KEYS = {"version", "note", "live", "bland_first_paid_call_approval"}
APPROVAL_KEYS = {"approved_by", "approved_on", "reference"}
PAID_CALL_APPROVER = "Charles"
# executor -> the registry field that holds its reviewed cancel target
TARGET_FIELD = {"browser": "cancel_url", "bland_call": "support_phone", "email": "cancel_email"}
E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class ExecutionPolicyError(GateError):
    """A live-execution gate refused to start a run."""


class LiveExecutionDisabledError(ExecutionPolicyError):
    pass


class PaidCallApprovalRequiredError(ExecutionPolicyError):
    pass


class FirstLiveApprovalRequiredError(ExecutionPolicyError):
    pass


class UnreviewedTargetError(ExecutionPolicyError):
    pass


BLOCKER_ERRORS = {
    "live_disabled": LiveExecutionDisabledError,
    "paid_call_needs_charles": PaidCallApprovalRequiredError,
    "first_live_needs_user_approval": FirstLiveApprovalRequiredError,
    "no_registry_merchant": UnreviewedTargetError,
    "unreviewed_target": UnreviewedTargetError,
    "target_mismatch": UnreviewedTargetError,
}


# -- config --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecutionConfig:
    live: Mapping[str, bool] = field(default_factory=lambda: {e: False for e in LIVE_EXECUTORS})
    bland_first_paid_call_approval: Mapping[str, str] | None = None
    error: str | None = None  # set when the file did not parse; everything is then off

    def is_live(self, executor: str) -> bool:
        return self.error is None and executor in LIVE_EXECUTORS and self.live.get(executor) is True

    def paid_call_approved(self) -> bool:
        return self.error is None and self.bland_first_paid_call_approval is not None


def _check_approval(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != APPROVAL_KEYS:
        raise ValueError(f"bland_first_paid_call_approval must be null or exactly {sorted(APPROVAL_KEYS)}")
    if value["approved_by"] != PAID_CALL_APPROVER:
        raise ValueError(f"only {PAID_CALL_APPROVER} can approve the first paid Bland call")
    if not isinstance(value["approved_on"], str):
        raise ValueError("approved_on must be an ISO date")
    date.fromisoformat(value["approved_on"])
    if not isinstance(value["reference"], str) or not value["reference"].strip():
        raise ValueError("reference must say where Charles approved it")
    return dict(value)


def parse_config(doc: Any) -> ExecutionConfig:
    """Strict parse. Raises ValueError on anything unexpected."""
    if not isinstance(doc, dict):
        raise ValueError("execution config must be an object")
    if doc.get("version", 1) != 1:
        raise ValueError("unsupported execution config version")
    unknown = set(doc) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"unknown execution config keys: {sorted(unknown)}")
    live = doc.get("live", {})
    if not isinstance(live, dict):
        raise ValueError("live must be an object")
    bad = set(live) - set(LIVE_EXECUTORS)
    if bad:
        raise ValueError(f"unknown live channels: {sorted(bad)}")
    for key, value in live.items():
        if not isinstance(value, bool):
            raise ValueError(f"live.{key} must be true or false")
    return ExecutionConfig(live={e: live.get(e, False) for e in LIVE_EXECUTORS},
                           bland_first_paid_call_approval=_check_approval(doc.get("bland_first_paid_call_approval")))


def load_config(path: str | None = None) -> ExecutionConfig:
    """Load ``config/execution.json``. Any problem returns an all-off config that records the error (fail closed)."""
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            return parse_config(json.load(fh))
    except Exception as exc:  # noqa: BLE001 - fail closed on anything
        return ExecutionConfig(error=f"{type(exc).__name__}: {exc}")


# -- reviewed registry ---------------------------------------------------------------------------------------------
_REGISTRY_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def load_registry(path: str | None = None) -> dict[str, dict[str, Any]]:
    path = path or REGISTRY_PATH
    if path not in _REGISTRY_CACHE:
        with open(path, encoding="utf-8") as fh:
            _REGISTRY_CACHE[path] = {m["id"]: m for m in json.load(fh)["merchants"]}
    return _REGISTRY_CACHE[path]


def valid_target(executor: str, value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if executor == "browser":
        return value.startswith("https://") and " " not in value
    if executor == "bland_call":
        return bool(E164.match(value))
    if executor == "email":
        return bool(EMAIL.match(value))
    return False


def reviewed_target(registry: Mapping[str, Mapping[str, Any]], merchant_id: str | None,
                    executor: str) -> tuple[str | None, list[dict[str, str]]]:
    """The reviewed cancel target for one channel, or the reasons there is none."""
    if not merchant_id or merchant_id not in registry:
        return None, [_blocker("no_registry_merchant", "the merchant is not in the reviewed merchant registry, so only "
                               "the manual guide is available")]
    entry = registry[merchant_id]
    problems = []
    if entry.get("verification") != "verified":
        problems.append(_blocker("unreviewed_target", f"the registry entry for {entry.get('name', merchant_id)} is not "
                                 "verified against official sources yet (Q11/D36)"))
    fld = TARGET_FIELD.get(executor)
    value = entry.get(fld) if fld else None
    if not valid_target(executor, value):
        problems.append(_blocker("unreviewed_target", f"the registry has no reviewed {fld or executor} for "
                                 f"{entry.get('name', merchant_id)}"))
        value = None
    return value, problems


# -- history -------------------------------------------------------------------------------------------------------
def merchant_for_request(store: Any, req: Mapping[str, Any]) -> str | None:
    if req.get("action_type") != "cancel_subscription":
        return None
    sub_id = (req.get("constraints") or {}).get("subscription_id") or req.get("target_ref")
    sub = store.get("subscriptions", sub_id) if isinstance(sub_id, str) else None
    return sub.get("merchant_id") if sub else None


def has_live_cancel_success(store: Any, merchant_id: str) -> bool:
    row = store.conn.execute(
        "SELECT 1 FROM live_runs l JOIN action_runs r ON r.id = l.run_id WHERE l.merchant_id = ? "
        "AND l.action_type = 'cancel_subscription' AND r.outcome = 'success' LIMIT 1", (merchant_id,)).fetchone()
    return row is not None


def has_live_bland_call(store: Any) -> bool:
    return store.conn.execute("SELECT 1 FROM live_runs WHERE executor = 'bland_call' LIMIT 1").fetchone() is not None


def _blocker(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


# -- the guard -----------------------------------------------------------------------------------------------------
class LiveGuard:
    """Called by ``ActionGate`` on create/amend (``annotate``) and before every run (``check`` + ``record``)."""

    def __init__(self, config: ExecutionConfig | None = None, registry: Mapping[str, Mapping[str, Any]] | None = None):
        self.config = config if config is not None else load_config()
        self._registry = registry

    @property
    def registry(self) -> Mapping[str, Mapping[str, Any]]:
        if self._registry is None:
            self._registry = load_registry()
        return self._registry

    def annotate(self, store: Any, row: dict[str, Any]) -> None:
        """D34: mark a cancel request whose merchant has never been cancelled live, so the card asks about it."""
        constraints = dict(row.get("constraints") or {})
        constraints.pop("first_live_cancel_merchant", None)  # only this method sets it
        merchant = None
        if row.get("action_type") == "cancel_subscription" and row.get("executor") in LIVE_EXECUTORS:
            merchant = merchant_for_request(store, row)
        if merchant and not has_live_cancel_success(store, merchant):
            constraints["first_live_cancel_merchant"] = merchant
        if constraints or row.get("constraints") is not None:
            row["constraints"] = constraints

    def preflight(self, store: Any, req: Mapping[str, Any]) -> list[dict[str, str]]:
        """Every reason a live run of this request could not start now (empty list = it could). Read-only."""
        executor = req.get("executor")
        if executor not in LIVE_EXECUTORS:
            return []
        blockers = []
        if not self.config.is_live(executor):
            why = f" ({self.config.error})" if self.config.error else ""
            blockers.append(_blocker("live_disabled", f"live {executor} execution is off in config/execution.json{why} "
                                     "(D33); use dry_run_action_request or the manual guide"))
        if executor == "bland_call" and not has_live_bland_call(store) and not self.config.paid_call_approved():
            blockers.append(_blocker("paid_call_needs_charles", "the first paid Bland call needs Charles's approval "
                                     "recorded in config/execution.json (D35)"))
        if req.get("action_type") == "cancel_subscription":
            merchant = merchant_for_request(store, req)
            target, problems = reviewed_target(self.registry, merchant, executor)
            blockers += problems
            constraints = req.get("constraints") or {}
            if target and constraints.get("cancel_target") != target:
                blockers.append(_blocker("target_mismatch", "constraints.cancel_target must be the reviewed registry "
                                         "target (use plan_cancellation); proposals and other sources are not allowed"))
            if merchant and not has_live_cancel_success(store, merchant) \
                    and constraints.get("first_live_cancel_merchant") != merchant:
                blockers.append(_blocker("first_live_needs_user_approval", f"this would be the first live "
                                         f"cancellation for {merchant}; the request must show it and be confirmed "
                                         "again (amend it)"))
        return blockers

    def check(self, store: Any, req: Mapping[str, Any]) -> None:
        blockers = self.preflight(store, req)
        if blockers:
            cls = BLOCKER_ERRORS.get(blockers[0]["code"], ExecutionPolicyError)
            raise cls("; ".join(b["message"] for b in blockers))

    def record(self, store: Any, req: Mapping[str, Any], run: Mapping[str, Any]) -> None:
        if req.get("executor") not in LIVE_EXECUTORS:
            return
        if store.find("live_runs", {"action_request_id": req["id"]}):
            return  # resumed run
        store.insert("live_runs", {"action_request_id": req["id"], "run_id": run["id"], "executor": req["executor"],
                                   "action_type": req["action_type"], "merchant_id": merchant_for_request(store, req),
                                   "started_at": to_iso(utcnow())})

    def status(self, store: Any) -> dict[str, Any]:
        return {"live": {e: self.config.is_live(e) for e in LIVE_EXECUTORS},
                "config_error": self.config.error,
                "first_paid_bland_call_approved": self.config.paid_call_approved(),
                "live_bland_call_placed": has_live_bland_call(store)}
