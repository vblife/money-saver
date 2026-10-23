"""Reviewer follow-ups: role phrases as names (review 5622969472, #8) and bare payment lines (review 5622966246, #7)."""
import pytest

from core.call_policy import CallPolicyError, build_call_script, check_name, validate_call_draft
from modules.statements import is_payment_descriptor, parse_csv_text

STEPS = ["Ask to cancel the subscription on this account."]

# -- (1) role phrases ------------------------------------------------------------------------------------------------
ROLE_NAMES = [
    "Customer Service", "User Support", "Owner Smith", "Subscriber Alex",          # the review's four examples
    "Holder Smith", "Member Lee",                     # cannot be told apart from 'Owner Smith': refused (D30)
    "Customer Care", "Customer Support Team", "Billing Department", "Account Owner", "Support Desk",
    "Client Services", "Alex Owner", "Users Smith", "Member Services", "Customer-Service", "Owner", "Service Team",
    "Billing Rep", "Help Desk Staff", "Subscribers", "Alex Customer Care",
]


@pytest.mark.parametrize("name", ROLE_NAMES)
def test_role_phrases_rejected_as_names(name):
    with pytest.raises(CallPolicyError):
        check_name(name)
    with pytest.raises(CallPolicyError):
        build_call_script(name, STEPS)
    with pytest.raises(CallPolicyError):
        build_call_script("Alex", STEPS, account_holder_name=name)


@pytest.mark.parametrize("name", ["Siri Hustvedt", "Dee Up", "Alexa Chung", "Rob Agent", "Agent Smith", "Sam Person",
                                  "Grace Son", "Ray Parent", "Ai Weiwei", "Will Smith", "Destiny Real"])
def test_real_names_still_accepted(name):
    assert check_name(name) == name
    validate_call_draft(build_call_script(name, STEPS), name)


@pytest.mark.parametrize("name", ["Alex Siri", "Siri", "Alexa"])
def test_voice_assistant_names_only_as_leading_word(name):
    with pytest.raises(CallPolicyError):
        check_name(name)


# -- (2) bare payment lines ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("desc", [
    "BILL PAY", "ONLINE BILL PAY", "ONLINE BILL PAY THANK YOU", "DIRECTPAY FULL BALANCE", "DIRECT PAY",
    "PAYPAL *PAYMENT", "PAYPAL * PAYMENT 1234 REF 99", "PAYMENT TO CARD 1234", "PAY MINIMUM DUE", "SQ *PAYMENT",
    "PMT 0412 REF 77", "PAYMENT", "AUTOPAY PAYMENT THANK YOU", "PAYPAL *REFUND",
])
def test_bare_payment_lines_are_payments(desc):
    assert is_payment_descriptor(desc) is True


@pytest.mark.parametrize("desc", [
    "GOOGLE *YOUTUBE PMT", "YOUTUBE PREMIUM PMT", "SPOTIFY PAYMENT", "NETFLIX.COM PAYMENT", "PAYPAL *HULU PAYMENT",
    "PAYPAL *PATREON MEMBERSHIP PMT", "ADOBE *CREATIVE CLD PMT", "APPLE.COM/BILL PMT", "APPLE.COM/BILL 866-712-7753",
    "DROPBOX PAYMENTS", "COACH PAYMENT", "PAYMENTUS NETFLIX", "PAY PAL", "XFINITY BILL", "BALANCE FITNESS",
])
def test_merchant_charges_still_charges(desc):
    assert is_payment_descriptor(desc) is False


@pytest.mark.parametrize("payment", ["BILL PAY", "DIRECTPAY FULL BALANCE", "PAYPAL *PAYMENT"])
def test_positive_bare_payment_flips_sign_convention(payment):
    # export where payments are positive and purchases negative
    rows = parse_csv_text(f"Date,Description,Amount\n08/11/2026,GOOGLE *YOUTUBE PMT,-13.99\n"
                          f"09/11/2026,GOOGLE *YOUTUBE PMT,-13.99\n08/20/2026,{payment},500.00\n", 2026)
    yt = [r["amount_cents"] for r in rows if "YOUTUBE" in r["description"]]
    pay = [r["amount_cents"] for r in rows if r["description"] == payment]
    assert yt == [1399, 1399] and pay == [-50000]


def test_negative_bare_payment_keeps_usual_convention():
    rows = parse_csv_text("Date,Description,Amount\n08/11/2026,SPOTIFY PAYMENT,11.99\n"
                          "08/20/2026,BILL PAY,-500.00\n", 2026)
    assert {r["description"]: r["amount_cents"] for r in rows} == {"SPOTIFY PAYMENT": 1199, "BILL PAY": -50000}
