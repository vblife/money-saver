"""D31: routines R1, R3, R4, R10 enabled by default; read/remind only; cancels stay behind the gate."""
import ast
import os

import pytest

from routines import schedule
from server.server import TOOLS, App

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTINE_TOOLS = {"scan_plan", "ingest_messages", "complete_scan", "refresh_after_scan", "dispatch_reminders",
                 "run_trial_guard", "expire_action_requests"}
GATE_EXECUTING = {"start_action_run", "finish_action_run", "confirm_action_request", "confirm_action_requests_batch",
                  "create_action_request", "amend_action_request", "preauthorize_trial_cancel", "ledger_add",
                  "record_evidence", "delete_all_data", "update_subscription", "respond_to_trial_reminder"}


@pytest.fixture
def app(tmp_path):
    a = App(str(tmp_path / "msb.db"), str(tmp_path / "ev"), str(tmp_path / "inbox"))
    yield a
    a.store.close()


def test_registry_has_the_four_routines_with_spec_times():
    got = {r["id"]: (r["name"], r["schedule"], r["cron"]) for r in schedule.ROUTINES}
    assert got == {
        "R1": ("daily_email_scan", {"kind": "daily", "at": "05:30"}, "30 5 * * *"),
        "R3": ("reminder_dispatch", {"kind": "hourly", "at": ":00"}, "0 * * * *"),
        "R4": ("trial_guard", {"kind": "daily", "at": "09:00"}, "0 9 * * *"),
        "R10": ("action_request_expiry", {"kind": "hourly", "at": ":15"}, "15 * * * *"),
    }
    assert all(r["enabled_by_default"] is True for r in schedule.ROUTINES)


def test_all_enabled_by_default_for_a_new_user(store):
    out = schedule.list_routines(store)
    assert [r["id"] for r in out["routines"] if r["enabled"]] == ["R1", "R3", "R4", "R10"]
    assert out["timezone"] == "America/Los_Angeles"
    assert "scheduled task" in out["register"]


def test_routines_only_use_read_and_remind_tools():
    names = {t["name"] for t in TOOLS}
    for r in schedule.ROUTINES:
        assert set(r["tools"]) <= ROUTINE_TOOLS, r["id"]
        assert set(r["tools"]) <= names
        assert not set(r["tools"]) & GATE_EXECUTING
        for banned in GATE_EXECUTING:
            assert banned not in r["prompt"]
        assert set(schedule.TOOL_TO_ROUTINE) <= ROUTINE_TOOLS


def test_routine_modules_never_execute_or_send():
    # No routine module may reach a run, an executor, or a network/send path.
    banned_attrs = {"begin_execution", "start_run", "finish_run", "send", "send_email", "place_call", "execute"}
    banned_imports = {"socket", "urllib", "http", "smtplib", "subprocess", "requests", "core.executors"}
    for fn in os.listdir(os.path.join(ROOT, "routines")):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(ROOT, "routines", fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned_attrs, (fn, node.attr)
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & banned_imports, fn
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") not in banned_imports and (node.module or "").split(".")[0] not in banned_imports, fn


def test_user_can_turn_off_r1_r3_r4_but_not_r10(store):
    for rid in ("R1", "R3", "R4"):
        assert schedule.set_enabled(store, rid, False)["enabled"] is False
        assert schedule.is_enabled(store, rid) is False
    with pytest.raises(ValueError):
        schedule.set_enabled(store, "R10", False)
    assert schedule.is_enabled(store, "R10") is True
    assert schedule.set_enabled(store, "R3", True)["enabled"] is True
    with pytest.raises(ValueError):
        schedule.set_enabled(store, "R99", True)
    with pytest.raises(ValueError):
        schedule.set_enabled(store, "R1", "no")


def test_turning_off_keeps_other_prefs(store):
    before = store.get_user()["notification_prefs"]
    schedule.set_enabled(store, "R4", False)
    after = store.get_user()["notification_prefs"]
    assert {k: v for k, v in after.items() if k != "routines"} == before
    assert after["routines"] == {"R4": False}


def test_missing_or_explicit_true_setting_means_on(store):
    prefs = dict(store.get_user()["notification_prefs"])
    prefs["routines"] = {"R1": True}
    store.update_user({"notification_prefs": prefs})
    assert all(schedule.is_enabled(store, r) for r in ("R1", "R3", "R4", "R10"))


def test_server_tools_list_and_toggle(app):
    names = {t["name"] for t in TOOLS}
    assert {"list_routines", "set_routine_enabled"} <= names
    out = app.call("list_routines", {})
    assert all(r["enabled"] for r in out["routines"])
    app.call("set_routine_enabled", {"routine_id": "R3", "enabled": False})
    assert {r["id"]: r["enabled"] for r in app.call("list_routines", {})["routines"]}["R3"] is False


def test_disabled_routine_is_skipped_only_when_scheduled(app):
    app.call("set_routine_enabled", {"routine_id": "R3", "enabled": False})
    assert app.call("dispatch_reminders", {"scheduled": True})["skipped"] is True
    manual = app.call("dispatch_reminders", {})
    assert "skipped" not in manual
    app.call("set_routine_enabled", {"routine_id": "R4", "enabled": False})
    assert app.call("run_trial_guard", {"scheduled": True})["skipped"] is True
    app.call("set_routine_enabled", {"routine_id": "R1", "enabled": False})
    assert app.call("refresh_after_scan", {"scheduled": True})["skipped"] is True
    # R10 always runs.
    assert "skipped" not in app.call("expire_action_requests", {"scheduled": True})


def test_enabled_scheduled_runs_execute_normally(app):
    for tool in ("refresh_after_scan", "dispatch_reminders", "run_trial_guard", "expire_action_requests"):
        assert "skipped" not in app.call(tool, {"scheduled": True})


def test_scheduled_routine_runs_never_start_action_runs(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.gate, "begin_execution", lambda *a, **k: calls.append(a))
    for tool in ("refresh_after_scan", "dispatch_reminders", "run_trial_guard", "expire_action_requests"):
        app.call(tool, {"scheduled": True})
    assert calls == [] and app.store.count("action_runs") == 0


def test_schemas_declare_scheduled_flag():
    by = {t["name"]: t for t in TOOLS}
    for tool in schedule.TOOL_TO_ROUTINE:
        assert by[tool]["inputSchema"]["properties"]["scheduled"]["type"] == "boolean"
    assert by["list_routines"]["annotations"]["readOnlyHint"] is True
