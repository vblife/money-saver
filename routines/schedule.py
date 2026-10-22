"""Routine schedule registry (spec 7.3; D31).

Charles approved on 2026-10-22: R1, R3, R4 and R10 run by default. The bot reads this registry with the
``list_routines`` MCP tool and registers each enabled routine as a Grok Bot scheduled task in the user's time zone.

These routines only read (Gmail via the bot's own connector, the local DB) and return texts for the user's own chat.
None of them sends anything to a third party, places a call, cancels a service, or starts an action run. A
cancellation always needs the user's explicit confirmation through the action gate.

A user can turn R1, R3 or R4 off (``notification_prefs.routines``; a missing value means on, values must be real
booleans). R10 only expires stale requests, which is the safe direction, so it cannot be turned off. R4 runs hourly
so a pre-authorized cancel is seen as ready at T-1 11:00; its user-facing texts are still keyed to 09:00 local.
"""
from __future__ import annotations

from typing import Any

from core.store import Store

ROUTINES: tuple[dict[str, Any], ...] = (
    {"id": "R1", "name": "daily_email_scan", "schedule": {"kind": "daily", "at": "05:30"}, "cron": "30 5 * * *",
     "tools": ["scan_plan", "ingest_messages", "complete_scan", "refresh_after_scan"],
     "prompt": ("money-saver R1: for each connected Gmail account, call scan_plan with scheduled=true. If it returns "
                "skipped, stop and do not read Gmail. Otherwise run each query with the Gmail connector (read only), "
                "pass the messages to ingest_messages, then complete_scan, then refresh_after_scan, each with "
                "scheduled=true. Post nothing unless the result has a text for the user."),
     "enabled_by_default": True, "user_can_disable": True},
    {"id": "R3", "name": "reminder_dispatch", "schedule": {"kind": "hourly", "at": ":00"}, "cron": "0 * * * *",
     "tools": ["dispatch_reminders"],
     "prompt": ("money-saver R3: call dispatch_reminders with scheduled=true and post each returned text to the user "
                "in this chat, unchanged."),
     "enabled_by_default": True, "user_can_disable": True},
    # Hourly (review of PR #4): a pre-authorized cancel becomes ready at T-1 11:00, two hours after the 09:00
    # warning; a daily-only run would not see it until T 09:00, possibly after the charge. User-facing times are
    # unchanged: reminders and the warning are still keyed to 09:00 local, so the other hourly runs post nothing.
    {"id": "R4", "name": "trial_guard", "schedule": {"kind": "hourly", "at": ":00"}, "cron": "0 * * * *",
     "tools": ["run_trial_guard"],
     "prompt": ("money-saver R4: call run_trial_guard with scheduled=true and post each returned text to the user in "
                "this chat, unchanged. Never start an action run from this routine."),
     "enabled_by_default": True, "user_can_disable": True},
    {"id": "R10", "name": "action_request_expiry", "schedule": {"kind": "hourly", "at": ":15"}, "cron": "15 * * * *",
     "tools": ["expire_action_requests"],
     "prompt": ("money-saver R10: call expire_action_requests with scheduled=true and post each returned text to the "
                "user in this chat, unchanged."),
     "enabled_by_default": True, "user_can_disable": False},
)
BY_ID = {r["id"]: r for r in ROUTINES}
# Tool name -> routine id. With scheduled=true, every tool of a disabled routine is skipped (R1: the whole scan,
# including scan_plan, so the bot reads no Gmail).
TOOL_TO_ROUTINE = {"scan_plan": "R1", "ingest_messages": "R1", "complete_scan": "R1", "refresh_after_scan": "R1",
                   "dispatch_reminders": "R3", "run_trial_guard": "R4", "expire_action_requests": "R10"}


def _prefs(store: Store) -> dict[str, Any]:
    return dict(store.get_user().get("notification_prefs") or {})


def validate_routine_prefs(value: Any) -> dict[str, bool]:
    """``notification_prefs.routines`` must map known routine ids to real booleans (not "false", 0, or None)."""
    if not isinstance(value, dict):
        raise ValueError("notification_prefs.routines must be an object of routine id -> true/false")
    for rid, v in value.items():
        if rid not in BY_ID:
            raise ValueError(f"unknown routine: {rid}")
        if not isinstance(v, bool):
            raise ValueError(f"notification_prefs.routines.{rid} must be true or false, not {v!r}")
        if v is False and not BY_ID[rid]["user_can_disable"]:
            raise ValueError(f"{rid} only expires stale requests and cannot be turned off")
    return dict(value)


def is_enabled(store: Store, routine_id: str) -> bool:
    """Strict: only a stored ``True`` or a missing value (the default, on) counts as enabled. ``False`` or any
    non-boolean value (for example the string "false", from data written before validation) counts as off."""
    r = BY_ID[routine_id]
    if not r["user_can_disable"]:
        return True
    routines = _prefs(store).get("routines")
    if routines is None:
        return r["enabled_by_default"]
    if not isinstance(routines, dict):
        return False
    if routine_id not in routines:
        return r["enabled_by_default"]
    return routines[routine_id] is True


def list_routines(store: Store) -> dict[str, Any]:
    tz = store.get_user().get("timezone") or "America/Los_Angeles"
    items = [{k: v for k, v in r.items()} | {"enabled": is_enabled(store, r["id"])} for r in ROUTINES]
    return {"timezone": tz, "routines": items,
            "register": ("Register every routine with enabled=true as a Grok Bot scheduled task at its schedule in "
                         "the user's time zone, using its prompt. Remove the task of a routine with enabled=false.")}


def set_enabled(store: Store, routine_id: str, enabled: bool) -> dict[str, Any]:
    if routine_id not in BY_ID:
        raise ValueError(f"unknown routine: {routine_id}")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if not BY_ID[routine_id]["user_can_disable"] and not enabled:
        raise ValueError(f"{routine_id} only expires stale requests and cannot be turned off")
    prefs = _prefs(store)
    prefs["routines"] = {**(prefs.get("routines") or {}), routine_id: enabled}
    store.update_user({"notification_prefs": prefs})
    return {"routine": routine_id, "enabled": is_enabled(store, routine_id),
            "next_step": ("Register this routine as a scheduled task." if enabled
                          else "Remove this routine's scheduled task.")}


def skipped(routine_id: str) -> dict[str, Any]:
    out = {"skipped": True, "routine": routine_id, "reason": "turned off by the user", "texts": []}
    if routine_id == "R1":
        out["instruction"] = "R1 is off: stop here and do not read Gmail."
    return out
