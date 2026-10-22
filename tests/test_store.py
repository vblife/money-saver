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
        store.insert("action_requests", {"action_type": "wire_money", "executor": "browser", "target_ref": "x", "summary": "x", "expires_at": "2026-01-01T00:00:00Z"})


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


# --- regression: JSON columns are scanned for forbidden data (review of PR #1, blocking 3) ---------
import pytest as _pytest  # noqa: E402

from core.store import TABLES as _TABLES  # noqa: E402

CARD = "4111 1111 1111 1111"
JSON_CASES = [
    ("action_requests", "plan_steps", ["type password: hunter2"]),
    ("action_requests", "constraints", {"card": CARD}),
    ("action_requests", "constraints", {"nested": {"deeper": [{"x": CARD}]}}),
    ("action_requests", "constraints", {"password": "hunter2"}),
    ("action_requests", "constraints", {CARD: "key holds the card"}),
    ("action_requests", "constraints", {"card_int": 4111111111111111}),
    ("action_requests", "disclosed_fields", [{"field": "ssn", "value_masked": "123-45-6789"}]),
    ("action_requests", "history", [{"note": "user said cvv: 123"}]),
    ("action_runs", "log", [{"typed": CARD}]),
    ("source_messages", "extracted", {"snippet": "SSN 123-45-6789"}),
    ("bookings", "travelers", ["Alex", "pin: 4321"]),
    ("users", "thresholds", {"x": CARD}),
]


@_pytest.mark.parametrize("table,column,value", JSON_CASES)
def test_json_columns_reject_forbidden_data(store, table, column, value):
    assert column in _TABLES[table][1]
    row = {
        "action_requests": {"action_type": "cancel_subscription", "target_ref": "s", "summary": "x", "executor": "browser", "expires_at": "2099-01-01T00:00:00Z"},
        "action_runs": {"action_request_id": "ar_x", "started_at": "2026-01-01T00:00:00Z", "executor": "browser"},
        "source_messages": {"gmail_account": "a@example.com", "message_id": "m", "category": "other", "confidence": 1},
        "bookings": {"kind": "flight"},
        "users": {},
    }[table]
    with _pytest.raises(ForbiddenDataError):
        store.insert(table, {**row, column: value})
    if table == "action_requests":
        ok = store.insert(table, row)
        with _pytest.raises(ForbiddenDataError):
            store.update(table, ok["id"], {column: value})


def test_every_json_column_is_scanned(store):
    # Guard against new JSON columns being added without coverage: encode each with a card number.
    for table, (_, cols) in _TABLES.items():
        for col in cols:
            with _pytest.raises(ForbiddenDataError):
                store._encode(table, {col: [{"v": CARD}]})


def test_json_columns_allow_masked_values(store):
    row = store.insert("action_requests", {"action_type": "cancel_subscription", "target_ref": "s", "summary": "x", "executor": "browser",
                                           "expires_at": "2099-01-01T00:00:00Z",
                                           "disclosed_fields": [{"field": "account_last4", "value_masked": "****4821"}],
                                           "constraints": {"max_monthly_cents": 5500, "no_contract": True}})
    assert row["constraints"]["max_monthly_cents"] == 5500


def test_ids_and_digests_never_trip_the_privacy_scanner(store):
    from core import privacy
    from core.util import new_id
    for _ in range(2000):
        assert not any(ch.isdigit() for ch in new_id("ar")[3:])
    # A digest that happens to contain a Luhn-valid 16-digit run must still be storable.
    digest = "4111111111111111" + "ab" * 24
    assert privacy.find_forbidden(digest)
    store.insert("evidence", {"kind": "form_receipt", "path_or_ref": "ref", "sha256": digest,
                              "captured_at": "2026-10-01T00:00:00Z"})
