"""Follow-ups to the PR #6 review (5618568698): precise payment filter, subscription count and order with fixed
expenses, strong subscription wording beats rent wording."""
from datetime import date

import pytest

from core.merchant_resolver import MerchantResolver
from core.notifier import format_subscription_list
from modules.statements import StatementImporter, is_payment_descriptor, parse_csv_text, parse_pdf_text
from modules.subscriptions import list_subscriptions, looks_like_fixed_expense

TODAY = date(2026, 10, 19)


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


# --- 1. precise payment filter -----------------------------------------------------------------------
@pytest.mark.parametrize("desc", [
    "GOOGLE *YOUTUBE PMT", "GOOGLE *YouTube Premium PMT", "YOUTUBE PREMIUM PMT", "SPOTIFY PAYMENT",
    "NETFLIX.COM PAYMENT", "PAYPAL *HULU PAYMENT", "PAYPAL *PATREON MEMBERSHIP PMT", "ADOBE *CREATIVE CLD PMT",
    "APPLE.COM/BILL PMT", "DROPBOX PAYMENTS", "EXPERIAN CREDIT MONITORING", "COACH PAYMENT", "PAYMENTUS NETFLIX",
])
def test_merchant_descriptors_with_pmt_or_payment_are_charges(desc):
    assert is_payment_descriptor(desc) is False


@pytest.mark.parametrize("desc", [
    "AUTOPAY", "AUTOPAY PAYMENT THANK YOU", "AUTO-PAY 123", "AUTOMATIC PAYMENT - THANK YOU", "PAYMENT THANK YOU",
    "PAYMENT - THANK YOU", "THANK YOU", "ONLINE PAYMENT", "ONLINE PAYMENT - THANK YOU", "MOBILE PYMT",
    "MOBILE PAYMENT", "PAYMENT RECEIVED", "PAYMENT", "PMT 0412 REF 77", "ACH PMT", "E-PAYMENT", "EPAYMENT",
    "CREDIT CARD PAYMENT", "CARD PAYMENT", "LOAN PAYMENT", "AUTO LOAN PMT", "INTERNET PAYMENT", "AMAZON REFUND",
    "STATEMENT CREDIT", "PAYPAL *REFUND",
])
def test_card_and_loan_payments_are_still_credits(desc):
    assert is_payment_descriptor(desc) is True


def test_review_repro_youtube_pmt_is_kept(importer, store, inbox):
    f = inbox / "s.csv"
    f.write_text("Date,Description,Amount\n08/11/2026,GOOGLE *YOUTUBE PMT,13.99\n09/11/2026,GOOGLE *YOUTUBE PMT,13.99\n"
                 "08/20/2026,AUTOPAY PAYMENT THANK YOU,-500.00\n09/20/2026,AUTOPAY PAYMENT THANK YOU,-500.00\n")
    res = importer.import_file(str(f), today=TODAY)
    assert res["kept"] == 2 and res["charges_added"] == 2
    assert all(p["amount_cents"] == 1399 for p in res["kept_preview"])
    assert store.count("subscriptions") == 1


def test_pmt_descriptor_does_not_drive_the_sign_convention():
    # purchases exported negative; the merchant "PMT" line must not be read as a positive payment anchor
    rows = parse_csv_text("Date,Description,Amount\n08/11/2026,GOOGLE *YOUTUBE PMT,-13.99\n"
                          "09/11/2026,GOOGLE *YOUTUBE PMT,-13.99\n08/20/2026,ONLINE PAYMENT,500.00\n", 2026)
    assert [r["amount_cents"] for r in rows if "YOUTUBE" in r["description"]] == [1399, 1399]


def test_pdf_youtube_pmt_is_a_charge():
    rows = parse_pdf_text("08/12  GOOGLE *YOUTUBE PMT   13.99\n08/13  PAYMENT THANK YOU   500.00\n", 2026)
    assert {(r["description"], r["amount_cents"]) for r in rows} == {("GOOGLE *YOUTUBE PMT", 1399),
                                                                      ("PAYMENT THANK YOU", -50000)}


# --- 3. strong subscription wording beats rent wording ------------------------------------------------
@pytest.mark.parametrize("desc,fixed", [
    ("APARTMENTS.COM PREMIUM subscription", False), ("APARTMENTS.COM PREMIUM", False), ("APARTMENTS.COM", False),
    ("ZILLOW RENTALS PREMIUM", False), ("RENTREDI SUBSCRIPTION", False), ("LANDLORD STUDIO MEMBERSHIP", False),
    ("OAKWOOD APARTMENTS RENT", True), ("MONTHLY RENT PAYMENT", True), ("SUNSET HOA DUES", True),
    ("WELLS FARGO MORTGAGE", True), ("ABC PROPERTY MGMT RECURRING", True),
])
def test_fixed_expense_detection_respects_subscription_signals(importer, desc, fixed):
    assert looks_like_fixed_expense(desc) is fixed
    assert importer.classify_line(desc)["fixed_expense"] is fixed


def test_apartments_com_premium_is_a_subscription(importer, store, inbox):
    f = inbox / "s.csv"
    f.write_text("Date,Description,Amount\n08/05/2026,APARTMENTS.COM PREMIUM subscription,39.99\n"
                 "09/05/2026,APARTMENTS.COM PREMIUM subscription,39.99\n")
    res = importer.import_file(str(f), today=TODAY)
    assert res["kept"] == 2 and res["not_subscriptions"] == 0
    assert not any(p["not_a_subscription"] for p in res["kept_preview"])
    listing = list_subscriptions(store)
    assert listing["not_subscriptions"] == [] and listing["subscription_count"] == 1
    assert listing["monthly_total_cents"] == 3999


# --- 2. count and order with fixed expenses --------------------------------------------------------------
MIXED = ("Date,Description,Amount\n"
         "08/01/2026,WELLS FARGO MORTGAGE,3200.00\n09/01/2026,WELLS FARGO MORTGAGE,3200.00\n"
         "08/02/2026,OAKWOOD APARTMENTS RENT,2100.00\n09/02/2026,OAKWOOD APARTMENTS RENT,2100.00\n"
         "08/03/2026,SPOTIFY USA,11.99\n09/03/2026,SPOTIFY USA,11.99\n"
         "08/04/2026,NETFLIX.COM,15.49\n09/04/2026,NETFLIX.COM,15.49\n")


def test_header_counts_only_real_subscriptions_and_fixed_expenses_sort_last(importer, store, inbox):
    f = inbox / "s.csv"
    f.write_text(MIXED)
    importer.import_file(str(f), today=TODAY)
    listing = list_subscriptions(store)
    flags = [s["not_a_subscription"] for s in listing["subscriptions"]]
    assert flags == [False, False, True, True]            # mortgage no longer sorts first
    assert listing["subscription_count"] == 2 and len(listing["not_subscriptions"]) == 2
    names = [s["display_name"] for s in listing["subscriptions"]]
    assert names[:2] == ["Netflix", "Spotify"] and "MORTGAGE" in names[2]   # each group still largest first
    for lang, header, section in (("en", "Your subscriptions (2)", "Not subscriptions"),
                                  ("zh", "你的订阅（2 项）", "以下不是订阅")):
        text = format_subscription_list(listing, lang).splitlines()
        assert text[0] == header
        cut = next(i for i, line in enumerate(text) if line.startswith(section))
        assert all("MORTGAGE" not in line and "RENT" not in line for line in text[:cut])
        assert sum(1 for line in text[cut + 1:] if line.startswith("• ")) == 2


def test_only_fixed_expenses_says_no_subscriptions(importer, store, inbox):
    f = inbox / "s.csv"
    f.write_text("Date,Description,Amount\n08/01/2026,WELLS FARGO MORTGAGE,3200.00\n"
                 "09/01/2026,WELLS FARGO MORTGAGE,3200.00\n")
    importer.import_file(str(f), today=TODAY)
    listing = list_subscriptions(store)
    text = format_subscription_list(listing, "en").splitlines()
    assert listing["subscription_count"] == 0
    assert text[0] == "I haven't found any subscriptions yet." and text[1].startswith("Not subscriptions")
    assert not any(line.startswith("Active total") for line in text)
