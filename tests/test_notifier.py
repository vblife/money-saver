from core import notifier


REQ = {"id": "ar_1", "action_type": "cancel_subscription", "summary": "Cancel Hulu (No Ads) $17.99/month on the web",
       "plan_steps": ["Open hulu.com account page", "Cancel", "Screenshot"], "disclosed_fields": [],
       "expected_saving_cents": 1799, "risk_notes": "Access continues until 10/24", "expires_at": "2026-10-22T20:00:00Z"}


def test_money():
    assert notifier.money(1799) == "$17.99" and notifier.money(128400) == "$1,284.00" and notifier.money(-5) == "-$0.05"


def test_action_card_shows_every_detail():
    text = notifier.format_action_request(REQ)
    for part in ("ar_1", "Cancel Hulu", "1. Open hulu.com", "$17.99", "Access continues", "[Confirm]"):
        assert part in text


def test_call_card_shows_disclosure_and_edit_button():
    req = {**REQ, "action_type": "negotiate_bill",
           "disclosed_fields": [{"field": "account_last4", "value_masked": "****4821"}]}
    text = notifier.format_action_request(req, "zh")
    assert "account_last4=****4821" in text and "[修改条件]" in text


def test_batch_lists_each_item():
    text = notifier.format_batch([REQ, {**REQ, "id": "ar_2", "summary": "Cancel Peacock"}])
    assert "1. [ar_1]" in text and "2. [ar_2] Cancel Peacock" in text


def test_month_headline_counts_only_verified():
    s = {"verified_cents": 14237, "pending_cents": 3600, "estimated_cents": 0, "travel_credit_cents": 4800, "points": 0}
    text = notifier.format_month_headline(s, "October")
    assert text.startswith("In October you saved $142.37 (verified)")
    assert "Travel credit (not counted): $48.00" in text


def test_card_shows_code_channel_and_exact_draft():
    req = {**REQ, "action_type": "send_email", "executor": "email", "confirmation_code": "QWERTY",
           "draft_content": "To: support@example.com\nPlease cancel my plan."}
    text = notifier.format_action_request(req)
    assert "How: email" in text and "  | Please cancel my plan." in text
    assert text.endswith("To approve, reply exactly: confirm QWERTY")
    assert "确认 QWERTY" in notifier.format_action_request(req, "zh")


def test_batch_lists_codes():
    text = notifier.format_batch([{**REQ, "confirmation_code": "AAAAAA"}, {**REQ, "id": "ar_2", "confirmation_code": "BBBBBB"}])
    assert "confirm AAAAAA BBBBBB" in text
