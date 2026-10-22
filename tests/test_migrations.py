"""Schema v1 -> v2 migration (re-review 5)."""
import json
import os
import sqlite3

import pytest

from core.action_gate import ALLOWED_EXECUTORS, ActionGate
from core.store import SCHEMA_VERSION, V1_DEFAULT_EXECUTOR, Store

V1_SQL = os.path.join(os.path.dirname(__file__), "fixtures", "schema_v1.sql")
NOW = "2026-10-19T20:00:00Z"


def make_v1_db(path):
    conn = sqlite3.connect(path)
    with open(V1_SQL, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.execute("PRAGMA user_version = 1")

    def ar(rid, atype, status, approved_msg=None):
        conn.execute(
            "INSERT INTO action_requests (id, action_type, target_ref, summary, plan_steps, status, approved_at,"
            " approved_by_message, expires_at, history, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, atype, "sub_x", f"{atype} {rid}", json.dumps(["step"]), status, NOW if approved_msg else None,
             approved_msg, "2099-01-01T00:00:00Z", json.dumps([{"to": status}]), NOW, NOW))
    ar("ar_pending", "cancel_subscription", "pending_user")
    ar("ar_approved", "place_call", "approved", "Confirm")
    ar("ar_executed", "send_email", "executed", "Confirm")
    ar("ar_rejected", "submit_claim", "rejected")
    conn.execute("INSERT INTO action_runs (id, action_request_id, started_at, finished_at, executor, outcome, log,"
                 " evidence_ids, created_at, updated_at) VALUES ('run_a','ar_executed',?,?,'email','success','[]','[]',?,?)",
                 (NOW, NOW, NOW, NOW))
    conn.execute("INSERT INTO users (id, created_at, updated_at) VALUES ('usr_default', ?, ?)", (NOW, NOW))
    conn.commit()
    conn.close()


def columns(conn, table):
    return [(r[1], r[2], r[3], r[4]) for r in conn.execute(f"PRAGMA table_info({table})")]


def test_schema_version_bumped():
    assert SCHEMA_VERSION == 2


def test_v1_database_migrates_to_v2(tmp_path):
    path = str(tmp_path / "v1.db")
    make_v1_db(path)
    store = Store(path)
    assert store.schema_version() == 2
    cols = {c[0] for c in columns(store.conn, "action_requests")}
    assert {"executor", "confirmation_code", "confirmation_text", "confirmation_sha256", "approval_source",
            "execute_by"} <= cols
    rows = {r["id"]: r for r in store.find("action_requests")}
    assert len(rows) == 4
    # fail closed: unbound v1 requests that were still live must be asked again
    assert rows["ar_pending"]["status"] == "expired" and rows["ar_approved"]["status"] == "expired"
    assert "migration" in rows["ar_pending"]["history"][-1]["note"]
    # terminal rows unchanged
    assert rows["ar_executed"]["status"] == "executed" and rows["ar_rejected"]["status"] == "rejected"
    assert rows["ar_executed"]["executor"] == "email" and rows["ar_approved"]["executor"] == "bland_call"
    # foreign keys from action_runs still resolve
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.find("action_runs")[0]["action_request_id"] == "ar_executed"
    store.close()


def test_migrated_schema_matches_fresh_schema(tmp_path):
    path = str(tmp_path / "v1.db")
    make_v1_db(path)
    migrated = Store(path)
    fresh = Store(str(tmp_path / "fresh.db"))
    for table in ("action_requests", "action_runs", "savings_ledger", "users"):
        assert columns(migrated.conn, table) == columns(fresh.conn, table), table
    idx = lambda c: sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"))  # noqa: E731
    assert idx(migrated.conn) == idx(fresh.conn)
    migrated.close()
    fresh.close()


def test_migrated_db_is_usable_and_reopen_is_idempotent(tmp_path):
    path = str(tmp_path / "v1.db")
    make_v1_db(path)
    Store(path).close()
    store = Store(path)
    assert store.schema_version() == 2 and store.count("action_requests") == 4
    gate = ActionGate(store)
    req = gate.create(action_type="cancel_subscription", target_ref="sub_new", summary="Cancel X", plan_steps=["a"])
    assert gate.confirm(req["id"], f"confirm {req['confirmation_code']}")["status"] == "approved"
    # an expired migrated request can never be approved or executed
    with pytest.raises(Exception):
        gate.confirm("ar_pending", "confirm", shown_text_sha256=None)
    with pytest.raises(Exception):
        gate.begin_execution("ar_approved", "bland_call")
    store.close()


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = str(tmp_path / "v1.db")
    make_v1_db(path)
    import core.store as st
    monkeypatch.setattr(st, "V1_DEFAULT_EXECUTOR", {"cancel_subscription": "teleport"})  # violates CHECK
    with pytest.raises(sqlite3.IntegrityError):
        Store(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "executor" not in {c[1] for c in conn.execute("PRAGMA table_info(action_requests)")}
    conn.close()


def test_newer_schema_is_refused(tmp_path):
    path = str(tmp_path / "future.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError):
        Store(path)


def test_v1_default_executor_map_matches_gate():
    assert V1_DEFAULT_EXECUTOR == {k: v[0] for k, v in ALLOWED_EXECUTORS.items()}


def test_pending_row_without_confirmation_binding_cannot_be_approved(tmp_path):
    """Even if an unbound row is pending (e.g. edited by hand), None == None must not approve it."""
    store = Store(str(tmp_path / "s.db"))
    gate = ActionGate(store)
    req = gate.create(action_type="cancel_subscription", target_ref="sub_x", summary="Cancel X", plan_steps=["a"])
    store.conn.execute("UPDATE action_requests SET confirmation_code=NULL, confirmation_text=NULL,"
                       " confirmation_sha256=NULL WHERE id=?", (req["id"],))
    store.conn.commit()
    for msg in ("confirm", "Confirm", "确认"):
        with pytest.raises(Exception, match="no bound confirmation"):
            gate.confirm(req["id"], msg, shown_text_sha256=None)
        with pytest.raises(Exception, match="no bound confirmation"):
            gate.confirm_batch([req["id"]], msg, shown_text_sha256=None)
    assert gate.get(req["id"])["status"] == "pending_user"
    store.close()
