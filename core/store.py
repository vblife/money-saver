"""SQLite access layer for the spec section 6 data model.

Single-user store. Money is stored as integer cents (``*_cents``), times as UTC ISO 8601
strings, and list/dict fields as JSON text. Every write goes through :class:`Store` so JSON
encoding, ``updated_at`` stamping, and sensitive-data redaction stay consistent.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Iterable

from . import privacy
from .util import default_db_path, new_id, to_iso, utcnow

SCHEMA_VERSION = 1
# Generated identifiers and digests: random hex can look like a card number, so they are not scanned.
UNSCANNED_COLUMNS = {"id", "sha256", "confirmation_sha256"}

# table -> (id prefix, JSON columns)
TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "users": ("usr", ("gmail_accounts", "household_emails", "states_lived", "cards_held",
                      "approved_pii_fields", "thresholds", "notification_prefs", "scan_cursor")),
    "source_messages": ("msg", ("extracted",)),
    "subscriptions": ("sub", ("source", "source_message_ids")),
    "charges": ("chg", ()),
    "bills": ("bil", ("source_message_ids",)),
    "orders": ("ord", ("source_message_ids",)),
    "order_items": ("itm", ()),
    "trips": ("trp", ()),
    "bookings": ("bkg", ("travelers", "segments", "hotel", "source_message_ids")),
    "travel_credits": ("tcr", ()),
    "price_trackers": ("ptr", ("urls", "price_history")),
    "claims": ("clm", ("evidence_ids",)),
    "reminders": ("rem", ()),
    "action_requests": ("ar", ("plan_steps", "disclosed_fields", "constraints", "history")),
    "action_runs": ("run", ("log", "evidence_ids")),
    "evidence": ("evd", ()),
    "savings_ledger": ("led", ("evidence_ids",)),
}

DDL = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
  gmail_accounts TEXT NOT NULL DEFAULT '[]', household_emails TEXT NOT NULL DEFAULT '[]',
  home_zip TEXT, states_lived TEXT NOT NULL DEFAULT '[]', cards_held TEXT NOT NULL DEFAULT '[]',
  approved_pii_fields TEXT NOT NULL DEFAULT '[]', thresholds TEXT NOT NULL DEFAULT '{}',
  notification_prefs TEXT NOT NULL DEFAULT '{}', scan_cursor TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

-- Gmail message index (PV-2: no full bodies, only structured fields + a short snippet).
CREATE TABLE IF NOT EXISTS source_messages (
  id TEXT PRIMARY KEY, gmail_account TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT,
  from_addr TEXT, subject TEXT, received_at TEXT, category TEXT NOT NULL, merchant_id TEXT,
  confidence REAL NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0, extracted TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(gmail_account, message_id));

CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY, merchant_id TEXT, display_name TEXT NOT NULL,
  billing_channel TEXT NOT NULL DEFAULT 'direct' CHECK (billing_channel IN ('direct','apple','google_play','paypal','amazon','carrier','other')),
  account_email TEXT, amount_cents INTEGER, currency TEXT NOT NULL DEFAULT 'USD',
  period TEXT NOT NULL DEFAULT 'unknown' CHECK (period IN ('weekly','monthly','quarterly','semiannual','annual','unknown')),
  monthly_equiv_cents INTEGER,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','trial','cancel_requested','cancel_in_progress','cancelled','cancel_unconfirmed','charged_after_cancel','paused','expired','ignored')),
  is_trial INTEGER NOT NULL DEFAULT 0, trial_end_date TEXT, trial_converts_to_cents INTEGER,
  next_charge_date TEXT, last_charge_date TEXT,
  last_used_signal TEXT NOT NULL DEFAULT 'unknown' CHECK (last_used_signal IN ('active','likely_unused','unknown')),
  last_used_evidence TEXT, duplicate_group_id TEXT, auto_cancel_before_trial INTEGER NOT NULL DEFAULT 0,
  user_decision TEXT NOT NULL DEFAULT 'none' CHECK (user_decision IN ('none','keep','cancel','remind_later')),
  confidence REAL NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT '["gmail"]', source_message_ids TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS charges (
  id TEXT PRIMARY KEY, subscription_id TEXT REFERENCES subscriptions(id), date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL, merchant_raw TEXT, source TEXT NOT NULL CHECK (source IN ('gmail','card_statement')),
  source_ref TEXT, card_last4 TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(source, source_ref, date, amount_cents));

CREATE TABLE IF NOT EXISTS bills (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL CHECK (category IN ('internet','mobile','tv','insurance_auto','insurance_home','insurance_renters','utility')),
  provider TEXT NOT NULL, account_last4 TEXT, monthly_cents INTEGER, plan_desc TEXT, contract_end_date TEXT,
  renewal_date TEXT, promo_end_date TEXT, last_negotiated_at TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','negotiating','negotiated','switched')),
  source_message_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, merchant_id TEXT, order_number TEXT, order_date TEXT, total_cents INTEGER,
  payment_last4 TEXT,
  ship_status TEXT NOT NULL DEFAULT 'ordered' CHECK (ship_status IN ('ordered','shipped','delivered','delayed','returned','cancelled')),
  promised_delivery_date TEXT, actual_delivery_date TEXT, return_deadline TEXT,
  return_deadline_confidence TEXT NOT NULL DEFAULT 'unknown', price_protection_until TEXT,
  source_message_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(merchant_id, order_number));

CREATE TABLE IF NOT EXISTS order_items (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), title TEXT NOT NULL, sku_or_asin TEXT,
  product_url TEXT, qty INTEGER NOT NULL DEFAULT 1, unit_price_cents INTEGER, returned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS trips (
  id TEXT PRIMARY KEY, name TEXT, start_date TEXT, end_date TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS bookings (
  id TEXT PRIMARY KEY, trip_id TEXT REFERENCES trips(id), kind TEXT NOT NULL CHECK (kind IN ('flight','hotel','car')),
  vendor_id TEXT, confirmation_code TEXT, travelers TEXT NOT NULL DEFAULT '[]', segments TEXT NOT NULL DEFAULT '[]',
  hotel TEXT NOT NULL DEFAULT '{}', total_cents INTEGER,
  paid_with TEXT NOT NULL DEFAULT 'cash' CHECK (paid_with IN ('cash','points','mixed')), points_amount INTEGER,
  changeable INTEGER NOT NULL DEFAULT 0, change_fee_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','changed','cancelled','completed')),
  source_message_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS travel_credits (
  id TEXT PRIMARY KEY, vendor_id TEXT, amount_cents INTEGER NOT NULL, expires_on TEXT, restrictions TEXT,
  origin_booking_id TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','used','expired')),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS price_trackers (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('wishlist','post_purchase','flight','hotel')),
  target_ref TEXT NOT NULL, urls TEXT NOT NULL DEFAULT '[]', baseline_cents INTEGER, target_cents INTEGER,
  last_price_cents INTEGER, lowest_price_cents INTEGER, price_history TEXT NOT NULL DEFAULT '[]',
  check_schedule TEXT NOT NULL DEFAULT 'daily' CHECK (check_schedule IN ('daily','twice_daily')),
  active_until TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','triggered','paused','expired','error')),
  consecutive_errors INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('price_adjustment','late_delivery','refund_followup','flight_compensation','travel_rebook_diff','class_action','unclaimed_property','charge_after_cancel_refund','other')),
  counterparty TEXT NOT NULL, related_ref TEXT, expected_amount_cents INTEGER, awarded_amount_cents INTEGER,
  deadline TEXT, channel TEXT CHECK (channel IN ('web_form','chat','phone','email','mail')), case_number TEXT,
  status TEXT NOT NULL DEFAULT 'identified' CHECK (status IN ('identified','awaiting_user','submitted','acknowledged','approved','denied','paid','expired','abandoned')),
  submitted_at TEXT, follow_up_at TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('trial_end','return_deadline','credit_expiry','points_expiry','gift_card_expiry','promo_end','insurance_renewal','claim_deadline','follow_up')),
  target_ref TEXT NOT NULL, fire_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','sent','dismissed','snoozed')),
  snooze_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(kind, target_ref, fire_at));

CREATE TABLE IF NOT EXISTS action_requests (
  id TEXT PRIMARY KEY,
  action_type TEXT NOT NULL CHECK (action_type IN ('cancel_subscription','negotiate_bill','place_call','submit_claim','send_email','rebook_travel','submit_form_with_pii','purchase')),
  target_ref TEXT NOT NULL, summary TEXT NOT NULL, plan_steps TEXT NOT NULL DEFAULT '[]',
  disclosed_fields TEXT NOT NULL DEFAULT '[]', constraints TEXT NOT NULL DEFAULT '{}', draft_content TEXT,
  expected_saving_cents INTEGER, risk_notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending_user' CHECK (status IN ('pending_user','approved','rejected','expired','executed','failed','cancelled_by_user')),
  approved_at TEXT, approved_by_message TEXT, expires_at TEXT NOT NULL,
  execute_not_before TEXT, history TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS action_runs (
  id TEXT PRIMARY KEY, action_request_id TEXT NOT NULL UNIQUE REFERENCES action_requests(id),
  started_at TEXT NOT NULL, finished_at TEXT,
  executor TEXT NOT NULL CHECK (executor IN ('browser','bland_call','email','manual')),
  outcome TEXT CHECK (outcome IN ('success','partial','failed','needs_user')), error_code TEXT,
  log TEXT NOT NULL DEFAULT '[]', evidence_ids TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('screenshot','confirmation_email','call_transcript','call_recording_url','form_receipt','chat_transcript','statement_line')),
  path_or_ref TEXT NOT NULL, sha256 TEXT, captured_at TEXT NOT NULL, related_ref TEXT, notes TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS savings_ledger (
  id TEXT PRIMARY KEY,
  module TEXT NOT NULL CHECK (module IN ('subscriptions','bills','pre_purchase','post_purchase','travel','recovery')),
  saving_type TEXT NOT NULL CHECK (saving_type IN ('recurring','one_time')), description TEXT NOT NULL,
  merchant_id TEXT, amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0), annualized_cents INTEGER,
  recurring_months INTEGER,
  form TEXT NOT NULL DEFAULT 'cash' CHECK (form IN ('cash','statement_credit','travel_credit','gift_card','points')),
  points_amount INTEGER,
  status TEXT NOT NULL CHECK (status IN ('estimated','pending','verified','reversed')),
  verification_ref TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]', source_ref TEXT NOT NULL, action_run_id TEXT,
  dedupe_key TEXT NOT NULL UNIQUE, reverses_id TEXT, occurred_at TEXT NOT NULL, verified_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_ar_status ON action_requests(status);
CREATE INDEX IF NOT EXISTS idx_led_status ON savings_ledger(status, occurred_at);
CREATE INDEX IF NOT EXISTS idx_msg_category ON source_messages(category);
CREATE INDEX IF NOT EXISTS idx_rem_fire ON reminders(status, fire_at);
"""


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    # -- schema -----------------------------------------------------------------------
    def migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self.conn.executescript(DDL)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.commit()

    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        self.conn.close()

    # -- generic CRUD ---------------------------------------------------------------
    @staticmethod
    def _check_table(table: str) -> None:
        if table not in TABLES:
            raise ValueError(f"unknown table: {table}")

    def _encode(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        json_cols = TABLES[table][1]
        out = {}
        for key, value in row.items():
            if key in json_cols:
                _scan_json(value, f"{table}.{key}")
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                privacy.assert_no_forbidden_secrets(value, field=f"{table}.{key}")
            elif isinstance(value, bool):
                value = int(value)
            elif isinstance(value, str) and key not in UNSCANNED_COLUMNS and not key.endswith("_id"):
                privacy.assert_no_forbidden_secrets(value, field=f"{table}.{key}")
            out[key] = value
        return out

    def _decode(self, table: str, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        json_cols = TABLES[table][1]
        out = dict(row)
        for key in json_cols:
            if key in out and out[key] is not None:
                out[key] = json.loads(out[key])
        return out

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        self._check_table(table)
        row = dict(row)
        row.setdefault("id", new_id(TABLES[table][0]))
        now = to_iso(utcnow())
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        enc = self._encode(table, row)
        cols = ", ".join(enc)
        marks = ", ".join("?" for _ in enc)
        with self.conn:
            self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(enc.values()))
        return self.get(table, row["id"])

    def get(self, table: str, row_id: str) -> dict[str, Any] | None:
        self._check_table(table)
        cur = self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        return self._decode(table, cur.fetchone())

    def update(self, table: str, row_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self._check_table(table)
        changes = dict(changes)
        changes["updated_at"] = to_iso(utcnow())
        enc = self._encode(table, changes)
        sets = ", ".join(f"{k} = ?" for k in enc)
        with self.conn:
            cur = self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*enc.values(), row_id])
        if cur.rowcount != 1:
            raise KeyError(f"{table}/{row_id} not found")
        return self.get(table, row_id)

    def find(self, table: str, where: dict[str, Any] | None = None, order_by: str = "created_at",
             limit: int | None = None) -> list[dict[str, Any]]:
        self._check_table(table)
        where = where or {}
        for key in where:
            if not key.isidentifier():
                raise ValueError(f"bad column: {key}")
        if not all(part.strip().split(" ")[0].isidentifier() for part in order_by.split(",")):
            raise ValueError(f"bad order_by: {order_by}")
        clause = " AND ".join(f"{k} = ?" for k in where)
        sql = f"SELECT * FROM {table}" + (f" WHERE {clause}" if clause else "") + f" ORDER BY {order_by}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur = self.conn.execute(sql, list(where.values()))
        return [self._decode(table, r) for r in cur.fetchall()]

    def count(self, table: str) -> int:
        self._check_table(table)
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # -- user settings ------------------------------------------------------------------
    def get_user(self) -> dict[str, Any]:
        users = self.find("users", limit=1)
        if users:
            return users[0]
        return self.insert("users", {"id": "usr_default", "thresholds": DEFAULT_THRESHOLDS,
                                     "notification_prefs": DEFAULT_NOTIFICATION_PREFS})

    def update_user(self, changes: dict[str, Any]) -> dict[str, Any]:
        user = self.get_user()
        allowed = set(USER_FIELDS)
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"unknown settings: {sorted(bad)}")
        if "cards_held" in changes:
            for card in changes["cards_held"]:
                last4 = str(card.get("last4", ""))
                if last4 and (len(last4) != 4 or not last4.isdigit()):
                    raise ValueError("cards_held stores only the last 4 digits")
        return self.update("users", user["id"], changes)

    def wipe_all(self) -> dict[str, int]:
        """PV-8: delete every row from every table. Returns counts deleted per table."""
        counts = {}
        with self.conn:
            self.conn.execute("PRAGMA foreign_keys = OFF")
            for table in TABLES:
                counts[table] = self.conn.execute(f"DELETE FROM {table}").rowcount
            self.conn.execute("PRAGMA foreign_keys = ON")
        return counts


def _scan_json(value: Any, field: str) -> None:
    """Spec 8.3: check every key and value of a JSON column, recursively, for forbidden data."""
    if isinstance(value, dict):
        for k, v in value.items():
            privacy.assert_no_forbidden_secrets(str(k), field=field)
            if isinstance(v, str) and isinstance(k, str):
                privacy.assert_no_forbidden_secrets(f"{k}: {v}", field=field)  # e.g. {"password": "hunter2"}
            _scan_json(v, field)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _scan_json(v, field)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (str, int, float)):
        privacy.assert_no_forbidden_secrets(str(value), field=field)
    else:
        raise TypeError(f"{field}: unsupported JSON value {type(value).__name__}")


USER_FIELDS = ("timezone", "gmail_accounts", "household_emails", "home_zip", "states_lived", "cards_held",
               "approved_pii_fields", "thresholds", "notification_prefs", "scan_cursor")

DEFAULT_THRESHOLDS = {
    "price_drop_min_cents": 500,      # FR-4-5: max($5, 5%)
    "price_drop_min_pct": 5,
    "travel_min_saving_cents": 2000,  # FR-5-4: $20
    "max_calls_per_day": 5,           # 7.3
}
DEFAULT_NOTIFICATION_PREFS = {
    "quiet_hours": ["22:00", "08:00"],  # R3
    "weekly_digest": True,
    "email_copy_to_self": False,        # Q7 default: chat + optional self email
    "gmail_labels": False,              # Q8 default off
}


def iter_rows(rows: Iterable[dict[str, Any]], *keys: str) -> list[tuple]:
    return [tuple(r[k] for k in keys) for r in rows]
