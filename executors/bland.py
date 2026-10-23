"""Bland AI cancellation calls (FR-1-11, D30, D35, spec 7.3 and 10.2).

Calls are asynchronous: ``place`` starts the gated run and queues the call; ``poll`` reads the call, audits the
transcript against D30, saves it as evidence, and closes the run when the call failed or the audit failed. A
completed call that passes the audit is left for the bot to judge from the transcript (did the merchant confirm the
cancellation?) and close with ``finish_action_run``.

The client is an interface. Tests use :class:`FakeBlandClient`. :class:`HttpBlandClient` is a thin adapter for the
documented v1 endpoints (``POST /v1/calls``, ``GET /v1/calls/{call_id}``, ``POST /v1/calls/{call_id}/stop``,
https://docs.bland.ai). Nothing constructs it by default: the server has no Bland client unless one is passed in,
so no paid call can be placed from this repository as shipped.

TODO(B3, Charles): the Bland account, the API key's deploy location, and the dispatch choice (plain ``task`` as here,
or a dashboard agent, persona, or pathway) are not decided. Until then ``HttpBlandClient`` is unused.
TODO(spec 10.2): "3 failed keypad (DTMF) attempts -> manual" needs a per-call IVR signal; the documented call
details do not expose one, so the bot applies it from the transcript and closes the run with error_code
``ivr_failed``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import timezone
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from core import call_policy
from core.action_gate import GateError
from core.util import parse_iso, utcnow

API_BASE = "https://api.bland.ai/v1"
API_KEY_ENV = "BLAND_API_KEY"
MAX_CALL_MINUTES = 60          # spec 10.2: hard cap per call
BUSINESS_HOURS = (9, 17)       # spec 7.3: local business hours of the merchant
DEFAULT_MAX_CALLS_PER_DAY = 5  # spec 7.3 (thresholds.max_calls_per_day)
TERMINAL = {"completed", "failed", "busy", "no-answer", "canceled", "unknown"}
ASKED_IF_AI = re.compile(r"\b(?:are you|is this|am i (?:talking|speaking) (?:to|with))\b[^?.!]{0,40}"
                         r"\b(?:a |an |the )?(?:ai|bot|robot|machine|recording|real person|human|computer|"
                         r"automated)\b", re.I)
ADMITS_AI = re.compile(r"\b(?:ai|artificial intelligence|assistant|bot|automated)\b", re.I)


class CallLimitError(GateError):
    """A call limit (spec 7.3) refused the call; nothing was dialled."""


class BlandUnavailableError(RuntimeError):
    pass


class CallClient(Protocol):
    def send_call(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...
    def get_call(self, call_id: str) -> dict[str, Any]: ...
    def stop_call(self, call_id: str) -> dict[str, Any]: ...


class FakeBlandClient:
    """Records payloads and replays scripted call details. Never touches the network."""

    def __init__(self, details: Mapping[str, Any] | None = None, fail_send: bool = False):
        self.sent: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.details = dict(details or {})
        self.fail_send = fail_send

    def send_call(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.fail_send:
            raise BlandUnavailableError("fake Bland outage")
        self.sent.append(dict(payload))
        return {"status": "success", "message": "Call successfully queued.", "call_id": f"fake-call-{len(self.sent)}",
                "batch_id": None}

    def get_call(self, call_id: str) -> dict[str, Any]:
        return {"call_id": call_id, "status": "queued", "completed": False, **self.details}

    def stop_call(self, call_id: str) -> dict[str, Any]:
        self.stopped.append(call_id)
        return {"status": "success"}


class HttpBlandClient:
    """Thin adapter for the documented Bland v1 call endpoints. Unused until B3 is decided (see module TODO)."""

    def __init__(self, api_key: str | None = None, base_url: str = API_BASE, timeout: float = 20.0,
                 opener: Callable[..., Any] = urllib.request.urlopen):
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise BlandUnavailableError(f"no Bland API key ({API_KEY_ENV} is not set)")
        self._key, self.base_url, self.timeout, self._open = key, base_url.rstrip("/"), timeout, opener

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method,
                                     headers={"authorization": f"Bearer {self._key}",
                                              "content-type": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except Exception as exc:  # noqa: BLE001 - any transport problem means "Bland unavailable"
            raise BlandUnavailableError(f"Bland request failed: {type(exc).__name__}") from exc

    def send_call(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/calls", payload)

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/calls/{call_id}")

    def stop_call(self, call_id: str) -> dict[str, Any]:
        return self._request("POST", f"/calls/{call_id}/stop", {})


# -- payload ---------------------------------------------------------------------------------------------------------
def build_payload(req: Mapping[str, Any], run_id: str, lang: str = "en") -> dict[str, Any]:
    """The documented ``POST /v1/calls`` body for an approved, gate-checked call request."""
    c = req.get("constraints") or {}
    target = c.get("cancel_target")
    if not isinstance(target, str) or not target.startswith("+"):
        raise GateError("the call request has no reviewed phone number in constraints.cancel_target")
    name = call_policy.check_name(c.get("call_user_name"))
    call_policy.validate_call_draft(req.get("draft_content"), name, c.get("account_holder_name"))
    opening = call_policy.build_call_instructions(name, lang=lang)["opening"]
    return {"phone_number": target, "task": req["draft_content"], "first_sentence": opening,
            "wait_for_greeting": True, "record": False, "max_duration": MAX_CALL_MINUTES,
            "voicemail": {"action": "hangup"},
            "guard_rails": [{"type": "tcpa:ai_disclosure", "actions": [{"type": "end_call"}],
                             "config": {"end_seconds": 30}}],
            "metadata": {"action_request_id": req["id"], "run_id": run_id}, "external_id": run_id}


# -- limits -----------------------------------------------------------------------------------------------------------
def check_limits(store: Any, now: Any, merchant_tz: str | None = None, exclude_run: str | None = None) -> None:
    """Spec 7.3: one call at a time, at most max_calls_per_day per day, merchant business hours 09:00-17:00."""
    user = store.get_user()
    tz = ZoneInfo(merchant_tz or user.get("timezone") or "America/Los_Angeles")
    local = now.astimezone(tz)
    if not BUSINESS_HOURS[0] <= local.hour < BUSINESS_HOURS[1]:
        raise CallLimitError(f"outside the merchant's business hours (09:00-17:00 {tz.key}); try again then")
    rows = store.conn.execute(
        "SELECT l.run_id, r.started_at, r.finished_at FROM live_runs l JOIN action_runs r ON r.id = l.run_id "
        "WHERE l.executor = 'bland_call'").fetchall()
    if any(r[2] is None and r[0] != exclude_run for r in rows):
        raise CallLimitError("another call is still in progress; only one call at a time")
    utz = ZoneInfo(user.get("timezone") or "America/Los_Angeles")
    today = now.astimezone(utz).date()
    count = sum(1 for r in rows if r[0] != exclude_run and parse_iso(r[1]).astimezone(utz).date() == today)
    limit = int((user.get("thresholds") or {}).get("max_calls_per_day", DEFAULT_MAX_CALLS_PER_DAY))
    if count >= limit:
        raise CallLimitError(f"the daily call limit ({limit}) is reached; try again tomorrow")


# -- transcript audit (D30) ----------------------------------------------------------------------------------------
def audit_transcript(details: Mapping[str, Any], user_name: str) -> dict[str, Any]:
    """Did the caller disclose up front, answer truthfully when asked, and never claim to be human or the user?"""
    turns = [t for t in details.get("transcripts") or [] if isinstance(t, dict) and t.get("user") in
             ("assistant", "user")]
    problems = []
    assistant = [t for t in turns if t["user"] == "assistant"]
    if assistant:
        first = assistant[0].get("text") or ""
        if "ai assistant" not in first.casefold():
            problems.append("the first thing the caller said did not disclose that it is an AI assistant")
    for t in assistant:
        try:
            call_policy.validate_script(t.get("text") or "", [user_name])
        except call_policy.CallPolicyError as exc:
            problems.append(f"the caller said something the honesty policy forbids: {exc}")
    for i, t in enumerate(turns):
        if t["user"] == "user" and ASKED_IF_AI.search(t.get("text") or ""):
            reply = next((x for x in turns[i + 1:] if x["user"] == "assistant"), None)
            if reply is None or not ADMITS_AI.search(reply.get("text") or ""):
                problems.append("asked whether it was an AI, the caller did not answer truthfully")
    return {"passed": not problems and bool(assistant), "problems": problems or
            ([] if assistant else ["the transcript has no caller turns to audit"]),
            "assistant_turns": len(assistant)}


def transcript_text(details: Mapping[str, Any]) -> str:
    if details.get("concatenated_transcript"):
        return str(details["concatenated_transcript"])
    return "\n".join(f"{t.get('user')}: {t.get('text')}" for t in details.get("transcripts") or []
                     if isinstance(t, dict))


# -- executor --------------------------------------------------------------------------------------------------------
class BlandCallExecutor:
    """Gated call flow. Not a ``GuardedExecutor`` subclass because a call spans two steps, but it uses the same
    gate methods: ``begin_execution`` (with the live guard) before dialling, ``finish_execution`` to close."""

    executor_name = "bland_call"

    def __init__(self, gate: Any, client: CallClient, evidence: Any, now_fn: Callable[[], Any] | None = None):
        self.gate, self.client, self.evidence = gate, client, evidence
        self.now_fn = now_fn or getattr(gate, "now_fn", utcnow)

    def place(self, request_id: str, lang: str = "en") -> dict[str, Any]:
        req = self.gate.require_approved(request_id, "bland_call")
        self.gate.live_guard.check(self.gate.store, req)  # fail before checking anything else
        merchant = (self.gate.live_guard.registry.get((req.get("constraints") or {}).get("merchant_id") or "")
                    or {})
        check_limits(self.gate.store, self.now_fn().astimezone(timezone.utc), merchant.get("timezone"))
        run = self.gate.begin_execution(request_id, "bland_call")
        try:
            payload = build_payload(req, run["id"], lang)
            resp = self.client.send_call(payload)
        except BlandUnavailableError as exc:
            return self._fail(run, "bland_unavailable", str(exc))
        if not isinstance(resp, dict) or resp.get("status") != "success" or not resp.get("call_id"):
            msg = resp.get("message") if isinstance(resp, dict) else None
            return self._fail(run, "bland_unavailable", f"Bland did not queue the call: {msg or 'no call_id'}")
        self.gate.append_run_log(run["id"], [{"bland_call_id": resp["call_id"], "queued": True,
                                              "max_duration_minutes": payload["max_duration"]}])
        return {"run_id": run["id"], "call_id": resp["call_id"], "status": "queued"}

    def _call_id(self, run: Mapping[str, Any]) -> str:
        ids = [e["bland_call_id"] for e in run.get("log") or [] if isinstance(e, dict) and e.get("bland_call_id")]
        if not ids:
            raise GateError(f"run {run['id']} has no Bland call")
        return ids[-1]

    def _fail(self, run: Mapping[str, Any], code: str, message: str) -> dict[str, Any]:
        closed = self.gate.finish_execution(run["id"], "failed", error_code=code,
                                            log=[{"error": message, "fallback": "manual_cancel_guide"}])
        return {"run_id": run["id"], "status": "failed", "error_code": code, "run": closed,
                "next_step": "Offer the manual guide (manual_cancel_guide)."}

    def poll(self, run_id: str) -> dict[str, Any]:
        run = self.gate.store.get("action_runs", run_id)
        if run is None or run["executor"] != "bland_call":
            raise GateError(f"{run_id} is not a call run")
        if run["finished_at"] is not None:
            return {"run_id": run_id, "status": "finished", "run": run}
        call_id = self._call_id(run)
        try:
            details = self.client.get_call(call_id)
        except BlandUnavailableError as exc:
            return {"run_id": run_id, "status": "unknown", "error": str(exc)}
        status = details.get("status")
        if not details.get("completed") and status not in TERMINAL:
            return {"run_id": run_id, "status": details.get("queue_status") or status or "in_progress"}
        req = self.gate.get(run["action_request_id"])
        name = (req.get("constraints") or {}).get("call_user_name") or ""
        ev = None
        text = transcript_text(details)
        if text.strip():
            ev = self.evidence.add_bytes("call_transcript", text.encode(), "call_transcript.txt", req["target_ref"],
                                         f"Bland call {call_id}")
        audit = audit_transcript(details, name)
        log = [{"bland_call_id": call_id, "status": status, "call_length_minutes": details.get("call_length"),
                "price": details.get("price"), "answered_by": details.get("answered_by"),
                "summary": details.get("summary"), "transcript_audit": audit}]
        if status != "completed":
            closed = self.gate.finish_execution(run_id, "failed", [ev["id"]] if ev else None,
                                                f"call_{(status or 'unknown').replace('-', '_')}", log)
            return {"run_id": run_id, "status": status, "run": closed, "audit": audit,
                    "next_step": "Offer the manual guide (manual_cancel_guide)."}
        if text.strip() and not audit["passed"]:
            closed = self.gate.finish_execution(run_id, "failed", [ev["id"]], "transcript_audit_failed", log)
            return {"run_id": run_id, "status": "audit_failed", "run": closed, "audit": audit,
                    "next_step": "Tell the user what went wrong and report the audit to Charles; do not call again "
                                 "until it is reviewed."}
        self.gate.append_run_log(run_id, log)
        return {"run_id": run_id, "status": "completed", "audit": audit,
                "transcript_evidence_id": ev["id"] if ev else None, "summary": details.get("summary"),
                "next_step": "Read the transcript. If the merchant confirmed the cancellation, call finish_action_run "
                             "with outcome success and the transcript evidence id; if a retention offer or a new "
                             "term came up, use needs_user and ask the user; otherwise failed."}

    def stop(self, run_id: str) -> dict[str, Any]:
        run = self.gate.store.get("action_runs", run_id)
        if run is None or run["executor"] != "bland_call" or run["finished_at"] is not None:
            raise GateError(f"{run_id} is not an open call run")
        call_id = self._call_id(run)
        try:
            self.client.stop_call(call_id)
        except BlandUnavailableError as exc:
            return {"run_id": run_id, "stopped": False, "error": str(exc)}
        closed = self.gate.finish_execution(run_id, "failed", error_code="call_stopped",
                                            log=[{"bland_call_id": call_id, "stopped": True}])
        return {"run_id": run_id, "stopped": True, "run": closed}

