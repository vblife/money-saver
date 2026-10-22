"""M1c card statements (FR-0-8, US-1-3, PV-3)."""
import json
import os
import shutil
from datetime import date

import pytest

from core.classifier import Classifier
from core.evidence import EvidencePathError
from core.gmail_scanner import ingest_messages
from core.merchant_resolver import MerchantResolver
from modules import statements as smod
from modules.statements import StatementError, StatementImporter, parse_csv_text, parse_pdf_text
from modules.subscriptions import SubscriptionInventory

TODAY = date(2026, 10, 19)
HAS_PDFTOTEXT = shutil.which("pdftotext") is not None


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


def make_pdf(lines):
    """A minimal one-page text PDF (no third-party libraries)."""
    esc = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    content = "BT /F1 9 Tf 40 760 Td 12 TL\n" + "".join(f"({line}) Tj T*\n" for line in esc) + "ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n"
    return out.encode()


PDF_LINES = [
    "ACME BANK VISA SIGNATURE   Account number ending 4417",
    "Statement period 08/05/2026 - 10/04/2026        New balance $1,234.56",
    "Alex Example, 12 Main St, Springfield",
    "08/12  08/13  NETFLIX.COM 866-579-7172 CA          15.49",
    "08/14  08/15  SAFEWAY #1234 SPRINGFIELD CA          82.17",
    "08/20  08/21  PAYPAL *HULU 402-935-7733             17.99",
    "08/28         SHELL OIL 57444 SPRINGFIELD CA        45.00",
    "09/02         GYMCO FITNESS 555-0100                40.00",
    "09/12  09/13  NETFLIX.COM 866-579-7172 CA          15.49",
    "09/15         PAYMENT THANK YOU                   -500.00",
    "09/20  09/21  PAYPAL *HULU 402-935-7733             17.99",
    "09/25         APPLE.COM/BILL 866-712-7753 CA         4.99",
    "10/02         GYMCO FITNESS 555-0100                40.00",
    "10/03         CORNER BAKERY                         12.30",
]


# --- parsing ------------------------------------------------------------------------------------------
def test_parse_pdf_text_with_period_year():
    text = "Statement period 12/05/2025 - 01/04/2026\n12/20 NETFLIX.COM 15.49\n01/02 SPOTIFY USA 11.99\n01/03 REFUND 5.00 CR"
    lines = parse_pdf_text(text, 2030)
    assert [(line["date"].isoformat(), line["amount_cents"]) for line in lines] == [
        ("2025-12-20", 1549), ("2026-01-02", 1199), ("2026-01-03", -500)]


@pytest.mark.parametrize("csv_text,expected", [
    ("Transaction Date,Post Date,Description,Category,Type,Amount\n"
     "10/03/2026,10/04/2026,NETFLIX.COM,Entertainment,Sale,-15.49\n"
     "10/05/2026,10/05/2026,Payment Thank You,,Payment,500.00\n",
     [("2026-10-03", "NETFLIX.COM", 1549), ("2026-10-05", "Payment Thank You", -50000)]),
    ("Date,Description,Debit,Credit,Card No.\n2026-10-03,SPOTIFY USA,11.99,,4111111111111111\n2026-10-04,REFUND,,5.00,4111111111111111\n",
     [("2026-10-03", "SPOTIFY USA", 1199), ("2026-10-04", "REFUND", -500)]),
    ("Account: 1234\n\nDate,Payee,Amount\n10/03/26,\"HULU, LLC\",$17.99\n",
     [("2026-10-03", "HULU, LLC", 1799)]),
])
def test_parse_csv_formats(csv_text, expected):
    lines = parse_csv_text(csv_text, 2026)
    assert [(line["date"].isoformat(), line["description"], line["amount_cents"]) for line in lines] == expected


def test_csv_keeps_only_last4_of_card_column():
    lines = parse_csv_text("Date,Description,Debit,Card No.\n2026-10-03,SPOTIFY USA,11.99,4111 1111 1111 1111\n", 2026)
    assert lines[0]["card_last4"] == "1111"


@pytest.mark.parametrize("bad", ["", "just,some,columns\n1,2,3\n", "Date,Description\n2026-10-01,X\n"])
def test_csv_without_required_columns(bad):
    with pytest.raises(StatementError):
        parse_csv_text(bad, 2026)


# --- recurring filter ----------------------------------------------------------------------------------
def test_only_likely_recurring_lines_are_kept(importer):
    lines = parse_pdf_text("\n".join(PDF_LINES), 2026)
    keep, dropped = importer.select_recurring(lines)
    kept = {(c["description"].split()[0], c["reason"]) for c in keep}
    assert ("NETFLIX.COM", "known subscription merchant") in kept
    assert ("PAYPAL", "known subscription merchant") in kept         # PayPal *HULU -> Hulu
    assert ("GYMCO", "repeats at a regular interval") in kept         # unknown merchant, monthly
    assert ("APPLE.COM/BILL", "known subscription merchant") in kept   # Apple billing, service not identified
    descs = " ".join(c["description"] for c in keep)
    for one_off in ("SAFEWAY", "SHELL", "CORNER BAKERY", "PAYMENT"):
        assert one_off not in descs
    assert dropped == len(lines) - len(keep)


def test_aggregator_prefixes_set_channel(importer):
    assert importer.classify_line("PAYPAL *HULU 402-935-7733")["channel"] == "paypal"
    assert importer.classify_line("PAYPAL *HULU 402-935-7733")["merchant_id"] == "hulu"
    apple = importer.classify_line("APPLE.COM/BILL 866-712-7753 CA")
    assert apple["merchant_id"] == "apple" and apple["channel"] == "apple"
    assert importer.classify_line("NETFLIX.COM 866-579-7172 CA")["channel"] == "direct"
    assert importer.classify_line("AMAZON PRIME*1A2B3C")["keyword"] is True


def test_irregular_repeats_are_not_recurring(importer):
    lines = [{"date": date(2026, 9, d), "description": "CORNER BAKERY", "amount_cents": 1230, "card_last4": None}
             for d in (1, 3, 9)]
    assert importer.select_recurring(lines)[0] == []


# --- import + linking ------------------------------------------------------------------------------------
def test_import_lines_creates_and_links_and_is_idempotent(store, resolver, importer):
    ingest_messages(store, "alex@example.com", [{
        "id": "n1", "from": "Netflix <billing@netflix.com>", "subject": "Your Netflix receipt",
        "body": "Thanks for your payment. You were charged $15.49 for your monthly Netflix plan on Jul 12, 2026.",
        "date": "2026-07-12T16:00:00Z"}], Classifier(resolver))
    SubscriptionInventory(store, resolver).build(TODAY)
    netflix = store.find("subscriptions", {"merchant_id": "netflix"})[0]
    lines = parse_pdf_text("\n".join(PDF_LINES), 2026)
    out = importer.import_lines(lines, TODAY)
    assert netflix["id"] in out["subscriptions_linked"]
    n = store.get("subscriptions", netflix["id"])
    assert n["source"] == ["card_statement", "gmail"] and n["last_charge_date"] == "2026-09-12"
    assert n["period"] == "monthly" and n["next_charge_date"] == "2026-11-12"   # Oct 12 passed: rolled forward
    gym = [s for s in store.find("subscriptions") if s["display_name"].startswith("GYMCO")]
    assert len(gym) == 1 and gym[0]["period"] == "monthly" and gym[0]["confidence"] == 0.5
    assert gym[0]["source"] == ["card_statement"] and gym[0]["next_charge_date"] == "2026-11-02"
    hulu = store.find("subscriptions", {"merchant_id": "hulu"})[0]
    assert hulu["billing_channel"] == "paypal" and hulu["account_email"] is None
    counts = (store.count("subscriptions"), store.count("charges"))
    again = importer.import_lines(lines, TODAY)
    assert again["charges_added"] == 0 and again["subscriptions_created"] == []
    assert (store.count("subscriptions"), store.count("charges")) == counts


def test_gmail_later_adopts_statement_only_subscription(store, resolver, importer):
    importer.import_lines(parse_pdf_text("\n".join(PDF_LINES), 2026), TODAY)
    hulu = store.find("subscriptions", {"merchant_id": "hulu"})[0]
    ingest_messages(store, "alex@example.com", [{
        "id": "h1", "from": "PayPal <service@paypal.com>", "subject": "Receipt for your payment to Hulu",
        "body": "You sent a payment of $17.99 to Hulu. Thanks for your payment. Billed monthly.",
        "date": "2026-10-20T16:00:00Z"}], Classifier(resolver))
    SubscriptionInventory(store, resolver).build(date(2026, 10, 21))
    rows = store.find("subscriptions", {"merchant_id": "hulu"})
    assert len(rows) == 1 and rows[0]["id"] == hulu["id"] and rows[0]["account_email"] == "alex@example.com"
    assert rows[0]["source"] == ["card_statement", "gmail"]
    assert len(store.find("charges", {"subscription_id": hulu["id"]})) == 3


def test_stored_rows_hold_only_minimal_fields(store, importer):
    importer.import_lines(parse_pdf_text("\n".join(PDF_LINES), 2026), TODAY)
    dump = json.dumps(store.find("charges")) + json.dumps(store.find("subscriptions"))
    for secret in ("4417", "1,234.56", "Main St", "SAFEWAY", "SHELL", "BAKERY", "PAYMENT THANK YOU", "Alex Example"):
        assert secret not in dump
    for c in store.find("charges"):
        assert c["source"] == "card_statement" and c["source_ref"].startswith("stmt_")
        assert set(c) >= {"date", "amount_cents", "merchant_raw", "card_last4"}


# --- files ------------------------------------------------------------------------------------------------
def test_csv_file_import_deletes_original_by_default(importer, inbox, store):
    f = inbox / "oct.csv"
    f.write_text("Date,Description,Amount,Card No.\n2026-09-03,SPOTIFY USA,11.99,4111111111111111\n"
                 "2026-10-03,SPOTIFY USA,11.99,4111111111111111\n2026-10-04,WHOLE FOODS,64.20,4111111111111111\n")
    out = importer.import_file(str(f), today=TODAY)
    assert out["file_type"] == "csv" and out["original_deleted"] is True and not f.exists()
    assert out["kept"] == 2 and out["dropped"] == 1
    assert {c["card_last4"] for c in store.find("charges")} == {"1111"}


def test_keep_original_on_request(importer, inbox):
    f = inbox / "keep.csv"
    f.write_text("Date,Description,Amount\n2026-10-03,SPOTIFY USA,11.99\n")
    out = importer.import_file(str(f), keep_original=True, today=TODAY)
    assert out["original_deleted"] is False and f.exists()


def test_parse_failure_still_deletes_and_says_so(importer, inbox):
    f = inbox / "junk.csv"
    f.write_bytes(b"\xff\xfe not text")
    with pytest.raises(StatementError, match="upload was deleted"):
        importer.import_file(str(f), today=TODAY)
    assert not f.exists()


def test_files_outside_inbox_symlinks_and_hardlinks_rejected(importer, inbox, tmp_path):
    outside = tmp_path / "secret.csv"
    outside.write_text("Date,Description,Amount\n2026-10-03,SPOTIFY USA,11.99\n")
    with pytest.raises(EvidencePathError):
        importer.import_file(str(outside), today=TODAY)
    link = inbox / "link.csv"
    link.symlink_to(outside)
    with pytest.raises(EvidencePathError):
        importer.import_file(str(link), today=TODAY)
    hard = inbox / "hard.csv"
    os.link(outside, hard)
    with pytest.raises(EvidencePathError):
        importer.import_file(str(hard), today=TODAY)
    assert outside.exists()   # never deleted: it was never accepted


def test_bad_card_last4(importer, inbox):
    f = inbox / "x.csv"
    f.write_text("Date,Description,Amount\n2026-10-03,SPOTIFY USA,11.99\n")
    with pytest.raises(StatementError, match="4 digits"):
        importer.import_file(str(f), card_last4="4111111111111111", today=TODAY)
    assert f.exists()   # rejected before opening


def test_oversize_rejected(importer, inbox, monkeypatch):
    monkeypatch.setattr(smod, "MAX_BYTES", 10)
    f = inbox / "big.csv"
    f.write_text("Date,Description,Amount\n" * 5)
    with pytest.raises(StatementError, match="larger than"):
        importer.import_file(str(f), today=TODAY)
    assert not f.exists()


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="poppler pdftotext not installed")
def test_pdf_file_import(importer, inbox, store):
    f = inbox / "stmt.pdf"
    f.write_bytes(make_pdf(PDF_LINES))
    out = importer.import_file(str(f), card_last4="4417", today=TODAY)
    assert out["file_type"] == "pdf" and out["original_deleted"] and not f.exists()
    assert out["kept"] >= 6 and out["lines_read"] == 11
    names = {s["display_name"].split()[0] for s in store.find("subscriptions")}
    assert {"Netflix", "Hulu", "GYMCO"} <= names
    assert {c["card_last4"] for c in store.find("charges")} == {"4417"}


def test_pdf_without_pdftotext(importer, inbox, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("pdftotext")
    monkeypatch.setattr(smod.subprocess, "run", boom)
    f = inbox / "s.pdf"
    f.write_bytes(make_pdf(["x"]))
    with pytest.raises(StatementError, match="not installed"):
        importer.import_file(str(f), today=TODAY)
