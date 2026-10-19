import sqlite3

import pytest

from core.privacy import ForbiddenDataError
from core.store import SCHEMA_VERSION, TABLES, Store


def test_schema_creates_all_section6_tables(store):
    assert store.schema_version() == SCHEMA_VERSION
    names = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= names


def test_migrate_is_idempotent(tmp_path):
    p = str(tmp_path / "x.db")
    Store(p).close()
    s = Store(p)
    assert s.schema_version() == SCHEMA_VERSION
    s.close()


def test_default_user_settings(store):
    u = store.get_user()
    assert u["timezone"] == "America/Los_Angeles"
    assert u["thresholds"]["price_drop_min_cents"] == 500
    assert u["notification_prefs"]["gmail_labels"] is False


def test_json_roundtrip_and_updated_at(store):
    sub = store.insert("subscriptions", {"display_name": "Netflix Standard", "merchant_id": "netflix", "amount_cents": 1549,
                                         "period": "monthly", "source_message_ids": ["m1", "m2"], "source": ["gmail"]})
    assert sub["source_message_ids"] == ["m1", "m2"] and sub["id"].startswith("sub_")
    upd = store.update("subscriptions", sub["id"], {"status": "cancel_requested"})
    assert upd["status"] == "cancel_requested"


def test_enum_constraints(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.insert("subscriptions", {"display_name": "X", "period": "fortnightly"})
    with pytest.raises(sqlite3.IntegrityError):
        store.insert("action_requests", {"action_type": "wire_money", "target_ref": "x", "summary": "x", "expires_at": "2026-01-01T00:00:00Z"})


def test_refuses_full_card_numbers_and_ssn(store):
    with pytest.raises(ForbiddenDataError):
        store.insert("charges", {"date": "2026-10-01", "amount_cents": 100, "source": "card_statement",
                                 "merchant_raw": "NETFLIX 4111111111111111"})
    with pytest.raises(ForbiddenDataError):
        store.insert("claims", {"type": "other", "counterparty": "State of CA", "case_number": "123-45-6789"})


def test_cards_held_last4_only(store):
    store.update_user({"cards_held": [{"issuer": "Chase", "product": "Sapphire", "last4": "1234"}]})
    with pytest.raises(ValueError):
        store.update_user({"cards_held": [{"issuer": "Chase", "last4": "4111111111111111"}]})


def test_unknown_setting_rejected(store):
    with pytest.raises(ValueError):
        store.update_user({"bank_password": "x"})


def test_reminder_dedupe(store):
    row = {"kind": "trial_end", "target_ref": "sub_1", "fire_at": "2026-10-24T16:00:00Z"}
    store.insert("reminders", row)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert("reminders", row)


def test_wipe_all(store):
    store.get_user()
    store.insert("subscriptions", {"display_name": "X"})
    counts = store.wipe_all()
    assert counts["subscriptions"] == 1
    assert all(store.count(t) == 0 for t in TABLES)


def test_find_rejects_injection(store):
    with pytest.raises(ValueError):
        store.find("subscriptions", {"1=1; DROP TABLE x; --": 1})
    with pytest.raises(ValueError):
        store.find("nope")
