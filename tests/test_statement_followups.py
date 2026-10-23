"""Follow-ups to the PR #5 review (non-blocking items 1-5): sign convention, huge CSV fields, atomic imports,
string inbox dirs, and rent labelled as not a subscription."""
import os
import subprocess
import sys
from datetime import date

import pytest

from core.evidence import EvidencePathError, EvidenceStore
from core.merchant_resolver import MerchantResolver
from core.notifier import format_subscription_list
from core.privacy import ForbiddenDataError
from core.store import Store
from modules.statements import StatementError, StatementImporter, parse_csv_text, parse_pdf_text
from modules.subscriptions import SubscriptionInventory, list_subscriptions

TODAY = date(2026, 10, 19)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def resolver():
    return MerchantResolver()


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    return d


@pytest.fixture
def importer(store, resolver, inbox, tmp_path):
    return StatementImporter(store, resolver, [str(inbox)], str(tmp_path / "evidence"))


def amounts(rows):
    return {(r["description"], r["amount_cents"]) for r in rows}


# --- 1. sign convention -----------------------------------------------------------------------------
REVIEW_CSV = ("Date,Description,Amount\n"
              "08/02/2026,AUTOPAY PAYMENT THANK YOU,-500.00\n"
              "09/02/2026,AUTOPAY PAYMENT THANK YOU,-500.00\n"
              "10/02/2026,AUTOPAY PAYMENT THANK YOU,-500.00\n"
              "09/14/2026,BEST BUY 00012345,899.00\n")


def test_payments_outnumbering_purchases_do_not_flip_signs():
    rows = parse_csv_text(REVIEW_CSV, 2026)
    assert ("BEST BUY 00012345", 89900) in amounts(rows)
    assert all(r["amount_cents"] < 0 for r in rows if "AUTOPAY" in r["description"])


def test_review_repro_autopay_is_never_a_subscription(importer, store, inbox):
    f = inbox / "s.csv"
    f.write_text(REVIEW_CSV)
    res = importer.import_file(str(f), today=TODAY)
    assert res["kept"] == 0 and store.count("subscriptions") == 0 and store.count("charges") == 0


def test_negative_purchase_export_is_detected_from_positive_payments():
    csv_text = ("Date,Description,Amount\n"
                "08/03/2026,SPOTIFY USA,-11.99\n09/03/2026,SPOTIFY USA,-11.99\n"
                "08/20/2026,ONLINE PAYMENT - THANK YOU,500.00\n09/20/2026,ONLINE PAYMENT - THANK YOU,500.00\n"
                "10/20/2026,ONLINE PAYMENT - THANK YOU,500.00\n")
    rows = parse_csv_text(csv_text, 2026)
    assert [r["amount_cents"] for r in rows if r["description"] == "SPOTIFY USA"] == [1199, 1199]
    assert all(r["amount_cents"] < 0 for r in rows if "PAYMENT" in r["description"])


def test_negative_purchase_export_without_payments():
    rows = parse_csv_text("Date,Description,Amount\n08/03/2026,SPOTIFY USA,-11.99\n09/03/2026,HULU,-7.99\n", 2026)
    assert {r["amount_cents"] for r in rows} == {1199, 799}


def test_ambiguous_signs_keep_the_usual_convention():
    # no payment anchors, mixed signs: charges stay positive (one refund line wins nothing by majority)
    rows = parse_csv_text("Date,Description,Amount\n08/03/2026,SPOTIFY USA,11.99\n08/04/2026,A,-1.00\n"
                          "08/05/2026,B,-2.00\n08/06/2026,C,-3.00\n", 2026)
    assert ("SPOTIFY USA", 1199) in amounts(rows)


@pytest.mark.parametrize("desc", ["AUTOPAY", "AUTO-PAY 123", "PAYMENT RECEIVED", "MOBILE PYMT", "THANK YOU",
                                  "AMAZON REFUND", "STATEMENT CREDIT"])
def test_payment_descriptors_are_credits_whatever_the_sign(desc):
    rows = parse_csv_text(f"Date,Description,Amount\n08/03/2026,{desc},25.00\n08/04/2026,NETFLIX.COM,15.49\n", 2026)
    assert [r["amount_cents"] for r in rows if r["description"] == desc] == [-2500]
    assert ("NETFLIX.COM", 1549) in amounts(rows)


def test_unsigned_export_with_payments_keeps_charges_positive():
    rows = parse_csv_text("Date,Description,Amount\n08/03/2026,SPOTIFY USA,11.99\n08/20/2026,AUTOPAY,500.00\n", 2026)
    assert amounts(rows) == {("SPOTIFY USA", 1199), ("AUTOPAY", -50000)}


def test_credit_monitoring_subscription_is_not_a_payment():
    rows = parse_csv_text("Date,Description,Amount\n08/03/2026,EXPERIAN CREDIT MONITORING,24.99\n", 2026)
    assert rows[0]["amount_cents"] == 2499


def test_type_column_still_wins():
    rows = parse_csv_text("Date,Description,Amount,Type\n08/03/2026,SPOTIFY USA,-11.99,Sale\n"
                          "08/04/2026,AUTOPAY,500.00,Payment\n08/05/2026,AUTOPAY,500.00,Payment\n", 2026)
    assert ("SPOTIFY USA", 1199) in amounts(rows) and all(r["amount_cents"] < 0 for r in rows if r["description"] == "AUTOPAY")


def test_pdf_payment_line_is_a_credit_even_without_cr():
    rows = parse_pdf_text("08/12  AUTOPAY PAYMENT THANK YOU   500.00\n08/13  NETFLIX.COM   15.49\n", 2026)
    assert amounts(rows) == {("AUTOPAY PAYMENT THANK YOU", -50000), ("NETFLIX.COM", 1549)}


# --- 2. oversized CSV field ---------------------------------------------------------------------------
def huge_csv():
    return "Date,Description,Amount\n08/03/2026,\"" + "X" * 140_000 + "\",11.99\n"


def test_huge_csv_field_is_a_clean_statement_error_and_upload_is_deleted(importer, store, inbox):
    f = inbox / "big.csv"
    f.write_text(huge_csv())
    with pytest.raises(StatementError, match="field is too long.*the upload was deleted"):
        importer.import_file(str(f), today=TODAY)
    assert not f.exists() and store.count("charges") == 0


def test_huge_csv_field_with_keep_original_keeps_the_file(importer, inbox):
    f = inbox / "big.csv"
    f.write_text(huge_csv())
    with pytest.raises(StatementError) as ei:
        importer.import_file(str(f), keep_original=True, today=TODAY)
    assert "deleted" not in str(ei.value) and f.exists()


def test_huge_csv_field_over_mcp_is_a_tool_error_not_internal(tmp_path):
    import json
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "big.csv"
    f.write_text(huge_csv())
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server", "server.py"), "--stdio", "--db",
                          str(tmp_path / "msb.db"), "--evidence-dir", str(tmp_path / "ev"), "--evidence-inbox",
                          str(inbox)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "import_card_statement", "arguments": {"path": str(f)}}}
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()
        resp = json.loads(p.stdout.readline())
    finally:
        p.stdin.close()
        p.wait(timeout=10)
    assert "error" not in resp, resp
    assert resp["result"]["isError"] is True
    assert "field is too long" in resp["result"]["content"][0]["text"]
    assert not f.exists()


# --- 3. atomic import ---------------------------------------------------------------------------------
def test_failed_line_rolls_back_the_whole_import(importer, store, inbox, monkeypatch):
    """Review repro: SPOTIFY stored, then the HULU line raised. Now nothing is stored."""
    f = inbox / "s.csv"
    f.write_text("Date,Description,Amount\n08/03/2026,SPOTIFY USA,11.99\n08/05/2026,HULU 877-8244858 CA,7.99\n")
    real_insert = store.insert

    def insert(table, row):
        if table == "charges" and "HULU" in (row.get("merchant_raw") or ""):
            raise ForbiddenDataError("refusing to store full_card_number in charges.merchant_raw")
        return real_insert(table, row)

    monkeypatch.setattr(store, "insert", insert)
    with pytest.raises(StatementError, match="nothing was imported.*the upload was deleted"):
        importer.import_file(str(f), today=TODAY)
    assert store.count("charges") == 0 and store.count("subscriptions") == 0
    assert not f.exists()
    monkeypatch.setattr(store, "insert", real_insert)
    store.insert("charges", {"date": "2026-08-01", "amount_cents": 1, "source": "card_statement", "source_ref": "x"})
    assert store.count("charges") == 1   # the store still commits normally afterwards


def test_import_rollback_also_undoes_updates_to_existing_rows(importer, store, inbox, monkeypatch):
    f = inbox / "a.csv"
    f.write_text("Date,Description,Amount\n08/03/2026,SPOTIFY USA,11.99\n")
    importer.import_file(str(f), today=TODAY)
    before = store.find("subscriptions")
    f2 = inbox / "b.csv"
    f2.write_text("Date,Description,Amount\n09/03/2026,SPOTIFY USA,11.99\n09/05/2026,HULU,7.99\n")
    real_insert = store.insert
    monkeypatch.setattr(store, "insert", lambda t, r: (_ for _ in ()).throw(RuntimeError("boom"))
                        if t == "charges" and "HULU" in (r.get("merchant_raw") or "") else real_insert(t, r))
    with pytest.raises(RuntimeError):
        importer.import_file(str(f2), today=TODAY)
    assert store.find("subscriptions") == before and store.count("charges") == 1


def test_store_transaction_nests_and_rolls_back(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    with pytest.raises(ValueError):
        with s.transaction():
            s.get_user()
            with s.transaction():
                s.insert("charges", {"date": "2026-08-01", "amount_cents": 1, "source": "card_statement", "source_ref": "a"})
            raise ValueError("x")
    assert s.count("charges") == 0 and s.count("users") == 0
    with s.transaction():
        s.insert("charges", {"date": "2026-08-01", "amount_cents": 1, "source": "card_statement", "source_ref": "b"})
    s.close()
    s = Store(str(tmp_path / "t.db"))
    assert s.count("charges") == 1
    s.close()


# --- 4. EvidenceStore inbox dirs --------------------------------------------------------------------------
def test_evidence_store_rejects_a_string_inbox(store, tmp_path):
    with pytest.raises(TypeError, match="list or tuple"):
        EvidenceStore(store, str(tmp_path / "ev"), str(tmp_path / "inbox"))
    with pytest.raises(TypeError):
        EvidenceStore(store, str(tmp_path / "ev"), [tmp_path / "inbox"])  # Path objects are not str


def test_evidence_store_rejects_filesystem_root(store, tmp_path):
    with pytest.raises(ValueError, match="filesystem root"):
        EvidenceStore(store, str(tmp_path / "ev"), ["/"])


def test_statement_importer_with_tuple_inbox_still_refuses_outside_paths(store, resolver, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outside = tmp_path / "secret.csv"
    outside.write_text("Date,Description,Amount\n08/03/2026,SPOTIFY USA,11.99\n")
    imp = StatementImporter(store, resolver, (str(inbox),), str(tmp_path / "ev"))
    with pytest.raises(EvidencePathError):
        imp.import_file(str(outside), today=TODAY)
    assert outside.exists()


def test_app_rejects_a_string_inbox(tmp_path):
    from server.server import App
    with pytest.raises(TypeError):
        App(str(tmp_path / "msb.db"), str(tmp_path / "ev"), str(tmp_path / "inbox"))


# --- 5. rent is not a subscription -----------------------------------------------------------------------
RENT_CSV = ("Date,Description,Amount\n"
            "08/01/2026,OAKWOOD APARTMENTS RENT,2100.00\n09/01/2026,OAKWOOD APARTMENTS RENT,2100.00\n"
            "08/03/2026,SPOTIFY USA,11.99\n09/03/2026,SPOTIFY USA,11.99\n")


def test_rent_is_kept_but_labelled_not_a_subscription(importer, store, inbox):
    f = inbox / "s.csv"
    f.write_text(RENT_CSV)
    res = importer.import_file(str(f), today=TODAY)
    assert res["not_subscriptions"] == 2
    rent = [p for p in res["kept_preview"] if "RENT" in p["description"]]
    assert rent and all(p["not_a_subscription"] and "not a subscription" in p["reason"] for p in rent)
    listing = list_subscriptions(store)
    by_name = {s["display_name"]: s for s in listing["subscriptions"]}
    rent_sub = next(s for n, s in by_name.items() if "RENT" in n)
    assert rent_sub["not_a_subscription"] is True and listing["not_subscriptions"] == [rent_sub["id"]]
    spotify = by_name["Spotify"]
    assert spotify["not_a_subscription"] is False
    assert listing["monthly_total_cents"] == spotify["monthly_equiv_cents"]   # rent not in the total
    text = format_subscription_list(listing, "en")
    total_line = next(line for line in text.splitlines() if line.startswith("Active total"))
    assert "Not subscriptions" in text and "$21" not in total_line
    assert "不是订阅" in format_subscription_list(listing, "zh")


@pytest.mark.parametrize("desc,fixed", [("SUNSET HOA DUES", True), ("ABC PROPERTY MGMT", True),
                                        ("RENT PAYMENT 1234", True), ("WELLS FARGO MORTGAGE", True),
                                        ("RENT THE RUNWAY", False), ("PARENTSQUARE", False), ("HERTZ RENTAL", False)])
def test_fixed_expense_wording(importer, desc, fixed):
    assert importer.classify_line(desc)["fixed_expense"] is fixed


def test_rent_increase_is_not_a_subscription_price_change(importer, store, inbox, resolver):
    f = inbox / "s.csv"
    f.write_text("Date,Description,Amount\n08/01/2026,OAKWOOD APARTMENTS RENT,2100.00\n"
                 "09/01/2026,OAKWOOD APARTMENTS RENT,2150.00\n")
    importer.import_file(str(f), today=TODAY)
    assert SubscriptionInventory(store, resolver).price_changes() == []
