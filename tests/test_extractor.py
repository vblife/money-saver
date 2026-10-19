from datetime import date

import pytest

from core.extractor import ExtractionSchemaError, extract, to_cents, validate_llm_extraction

REF = date(2026, 10, 15)


def test_to_cents():
    assert to_cents("15.49") == 1549 and to_cents("1,234.50") == 123450 and to_cents("7") == 700


def test_receipt_fields_have_evidence_spans():
    text = "Hi, you were charged $15.49 for your monthly Netflix plan on Oct 3, 2026. Next billing date: Nov 3, 2026."
    out = extract(text, "m1", REF)
    assert out["amount_cents"]["value"] == 1549
    assert out["period"]["value"] == "monthly"
    assert out["next_charge_date"]["value"] == "2026-11-03"
    for f in out.values():
        assert f["evidence_span"] in text and f["source_message_id"] == "m1"


def test_trial_end_and_days():
    out = extract("Your 7-day free trial has started. After your trial ends on Oct 26, 2026 you'll be charged $9.99/month.", "m2", REF)
    assert out["trial_end_date"]["value"] == "2026-10-26"
    assert out["trial_days"]["value"] == 7
    assert out["amount_cents"]["value"] == 999


def test_year_inferred_for_dates_without_year():
    out = extract("Arrives by Jan 4", "m3", date(2026, 12, 20))
    assert out["date"]["value"] == "2027-01-04"


def test_order_confirmation_and_last4():
    out = extract("Order # 300-118822 total $64.10 charged to card ending 1111. Confirmation code: ABC123", "m4", REF)
    assert out["order_number"]["value"] == "300-118822"
    assert out["last4"]["value"] == "1111"
    assert out["confirmation_code"]["value"] == "ABC123"


def test_no_invented_fields():
    assert extract("Thanks for being a customer!", "m5", REF) == {}


def test_extraction_on_all_fixture_receipts_finds_amount(samples):
    for r in samples:
        if r["category"] == "subscription_receipt":
            out = extract(r["subject"] + "\n" + r["body"], r["id"], REF)
            assert out["amount_cents"]["value"] == to_cents(r["amount"]), r["id"]


# --- 7.6 LLM validation ----------------------------------------------------------------------------
TEXT = "Your plan renews on Nov 15, 2026. You will be charged $11.99."


def test_llm_fields_with_matching_spans_are_kept():
    clean, dropped = validate_llm_extraction({
        "amount_cents": {"value": 1199, "evidence_span": "$11.99"},
        "next_charge_date": {"value": "2026-11-15", "evidence_span": "Nov 15, 2026"}}, TEXT, "m9")
    assert set(clean) == {"amount_cents", "next_charge_date"} and dropped == []


def test_llm_hallucinated_values_are_dropped():
    clean, dropped = validate_llm_extraction({
        "amount_cents": {"value": 2999, "evidence_span": "$29.99"},          # span not in text
        "next_charge_date": {"value": "2026-12-15", "evidence_span": "Nov 15, 2026"},  # value != span
        "period": {"value": "fortnightly", "evidence_span": "renews"},
        "made_up": {"value": "x", "evidence_span": "plan"}}, TEXT)
    assert clean == {} and len(dropped) == 4


def test_llm_schema_failure_raises():
    with pytest.raises(ExtractionSchemaError):
        validate_llm_extraction(["not", "a", "dict"], TEXT)
    with pytest.raises(ExtractionSchemaError):
        validate_llm_extraction({"amount_cents": 1199}, TEXT)
