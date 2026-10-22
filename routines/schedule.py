"""Routine schedule registry (spec 7.3; D31).

Charles approved on 2026-10-22: R1, R3, R4 and R10 run by default. The bot reads this registry with the
``list_routines`` MCP tool and registers each enabled routine as a Grok Bot scheduled task in the user's time zone.

These routines only read (Gmail via the bot's own connector, the local DB) and return texts for the user's own chat.
None of them sends anything to a third party, places a call, cancels a service, or starts an action run. A
cancellation always needs the user's explicit confirmation through the action gate.

A user can turn R1, R3 or R4 off (``notification_prefs.routines``; a missing value means on). R10 only expires stale
requests, which is the safe direction, so it cannot be turned off.
"""
from __future__ import annotations

from typing import Any

from core.store import Store

ROUTINES: tuple[dict[str, Any], ...] = (
    {"id": "R1", "name": "daily_email_scan", "schedule": {"kind": "daily", "at": "05:30"}, "cron": "30 5 * * *",
     "tools": ["scan_plan", "ingest_messages", "complete_scan", "refresh_after_scan"],
     "prompt": ("money-saver R1: for each connected Gmail account, call scan_plan, run each query with the Gmail "
                "connector (read only), pass the messages to ingest_messages, then complete_scan, then "
                "refresh_after_scan with scheduled=true. Post nothing unless the result has a text for the user."),
     "enabled_by_default": True, "user_can_disable": True},
    {"id": "R3", "name": "reminder_dispatch", "schedule": {"kind": "hourly", "at": ":00"}, "cron": "0 * * * *",
     "tools": ["dispatch_reminders"],
     "prompt": ("money-saver R3: call dispatch_reminders with scheduled=true and post each returned text to the user "
                "in this chat, unchanged."),
     "enabled_by_default": True, "user_can_disable": True},
    {"id": "R4", "name": "trial_guard", "schedule": {"kind": "daily", "at": "09:00"}, "cron": "0 9 * * *",
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
# Tool name -> routine id, for the tools a scheduled run calls last (the one that may be skipped when disabled).
TOOL_TO_ROUTINE = {"refresh_after_scan": "R1", "dispatch_reminders": "R3", "run_trial_guard": "R4",
                   "expire_action_requests": "R10"}


def _prefs(store: Store) -> dict[str, Any]:
    return dict(store.get_user().get("notification_prefs") or {})


def is_enabled(store: Store, routine_id: str) -> bool:
    r = BY_ID[routine_id]
    if not r["user_can_disable"]:
        return True
    value = (_prefs(store).get("routines") or {}).get(routine_id)
    return r["enabled_by_default"] if value is None else bool(value)


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
    return {"skipped": True, "routine": routine_id, "reason": "turned off by the user", "texts": []}
