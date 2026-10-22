"""M1a subscription inventory (FR-1-2..1-5, FR-1-16, US-1-1)."""
import json
import os
from datetime import date

import pytest

from core import notifier
from core.classifier import Classifier
from core.extractor import extract, validate_llm_extraction
from core.gmail_scanner import ingest_messages
from core.merchant_resolver import MerchantResolver
from modules import subscriptions as sm
from modules.subscriptions import SubscriptionInventory, add_months, infer_period, monthly_equiv

TODAY = date(2026, 10, 19)
ACCT = "alex@example.com"
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "emails")


@pytest.fixture(scope="module")
def resolver():
    return MerchantResolver()


def receipt(mid, merchant, domain, amount, day, period_word="monthly", next_date=None, frm=None, extra=""):
    body = (f"Hi Alex, thanks for your payment. You were charged ${amount} for your {period_word} {merchant} plan "
            f"on {day}.{' Next billing date: ' + next_date + '.' if next_date else ''} {extra}")
    return {"id": mid, "from": frm or f"{merchant} <billing@{domain}>", "subject": f"Your {merchant} receipt",
            "body": body, "date": _iso(day)}


def _iso(day):
    from datetime import datetime
    return datetime.strptime(day, "%b %d, %Y").strftime("%Y-%m-%dT16:00:00Z")


def ingest(store, resolver, messages, account=ACCT):
    return ingest_messages(store, account, messages, Classifier(resolver))


def build(store, resolver, today=TODAY):
    return SubscriptionInventory(store, resolver).build(today)


def only(store, **where):
    rows = store.find("subscriptions", where)
    assert len(rows) == 1, rows
    return rows[0]


# --- pure helpers -------------------------------------------------------------------------------
@pytest.mark.parametrize("gaps,expected", [
    ([7, 7, 7], "weekly"), ([31, 30, 31], "monthly"), ([28, 31], "monthly"), ([92, 91], "quarterly"),
    ([182], "semiannual"), ([365], "annual"), ([366], "annual"), ([45], None), ([200], None), ([], None),
])
def test_infer_period_from_gaps(gaps, expected):
    from datetime import timedelta
    days = [date(2025, 1, 1)]
    for g in gaps:
        days.append(days[-1] + timedelta(days=g))
    assert infer_period(days) == expected


def test_infer_period_uses_distinct_dates_and_median():
    d = [date(2026, 1, 1), date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 3, 9)]
    assert infer_period(d) == "monthly"  # gaps 31, 28, 8 -> median 28


def test_add_months_clamps_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


@pytest.mark.parametrize("amount,period,expected", [
    (1549, "monthly", 1549), (11988, "annual", 999), (2997, "quarterly", 999), (500, "weekly", 2167),
    (6000, "semiannual", 1000), (999, "unknown", None), (None, "monthly", None)])
def test_monthly_equiv(amount, period, expected):
    assert monthly_equiv(amount, period) == expected


# --- extractor additions --------------------------------------------------------------------------
def test_extract_price_change_from_to():
    f = extract("Your Netflix price is changing from $15.49 to $17.99 per month on Dec 1, 2026.", "m")
    assert f["price_old_cents"]["value"] == 1549 and f["price_new_cents"]["value"] == 1799
    assert "$17.99" in f["price_new_cents"]["evidence_span"]


def test_extract_price_increase_to():
    f = extract("Heads up: your plan price will increase to $22.99/month at your next renewal.", "m")
    assert f["price_new_cents"]["value"] == 2299 and "price_old_cents" not in f


@pytest.mark.parametrize("text,signal", [
    ("We miss you! Come back to Hulu and save.", "likely_unused"),
    ("It's been a while since you visited.", "likely_unused"),
    ("New sign-in to your Spotify account", "active"),
    ("Your weekly summary: you watched 4 hours", "active"),
    ("Your receipt from Netflix", None),
])
def test_extract_usage_signal(text, signal):
    f = extract(text, "m")
    assert (f.get("usage_signal") or {}).get("value") == signal


def test_llm_usage_and_price_fields_validated():
    text = "We miss you. Your price will increase to $9.99."
    clean, dropped = validate_llm_extraction({
        "usage_signal": {"value": "likely_unused", "evidence_span": "We miss you"},
        "price_new_cents": {"value": 999, "evidence_span": "increase to $9.99"}}, text)
    assert set(clean) == {"usage_signal", "price_new_cents"} and not dropped
    clean, dropped = validate_llm_extraction({
        "usage_signal": {"value": "loves_it", "evidence_span": "We miss you"},
        "price_new_cents": {"value": 1999, "evidence_span": "increase to $9.99"}}, text)
    assert clean == {} and len(dropped) == 2


# --- inventory ----------------------------------------------------------------------------------
def test_monthly_charges_give_period_next_date_and_charges(store, resolver):
    ingest(store, resolver, [
        receipt("n1", "Netflix", "netflix.com", "15.49", "Aug 3, 2026"),
        receipt("n2", "Netflix", "netflix.com", "15.49", "Sep 3, 2026"),
        receipt("n3", "Netflix", "netflix.com", "15.49", "Oct 3, 2026", next_date="Nov 3, 2026"),
    ])
    out = build(store, resolver)
    assert out["charges_added"] == 3 and len(out["created"]) == 1
    s = only(store, merchant_id="netflix")
    assert s["period"] == "monthly" and s["amount_cents"] == 1549 and s["monthly_equiv_cents"] == 1549
    assert s["last_charge_date"] == "2026-10-03" and s["next_charge_date"] == "2026-11-03"
    assert s["billing_channel"] == "direct" and s["account_email"] == ACCT and s["status"] == "active"
    assert s["source"] == ["gmail"] and s["source_message_ids"] == ["n1", "n2", "n3"]
    charges = store.find("charges", {"subscription_id": s["id"]})
    assert sorted(c["date"] for c in charges) == ["2026-08-03", "2026-09-03", "2026-10-03"]
    assert all(c["source"] == "gmail" for c in charges)


def test_single_annual_receipt_uses_text_period(store, resolver):
    ingest(store, resolver, [receipt("d1", "Dropbox", "dropbox.com", "119.88", "Mar 10, 2026", period_word="annual")])
    build(store, resolver)
    s = only(store, merchant_id="dropbox")
    assert s["period"] == "annual" and s["monthly_equiv_cents"] == 999
    assert s["next_charge_date"] == "2027-03-10"  # last charge + 1 year (FR-1-4)


def test_two_charges_beat_text_period(store, resolver):
    # The wording says "annual", but the charges are a month apart.
    ingest(store, resolver, [receipt("x1", "Spotify", "spotify.com", "11.99", "Aug 20, 2026", period_word="annual"),
                             receipt("x2", "Spotify", "spotify.com", "11.99", "Sep 20, 2026", period_word="annual")])
    build(store, resolver)
    assert only(store, merchant_id="spotify")["period"] == "monthly"


def test_computed_next_charge_rolls_forward_to_today(store, resolver):
    ingest(store, resolver, [receipt("h1", "Hulu", "hulu.com", "17.99", "Aug 25, 2026"),
                             receipt("h2", "Hulu", "hulu.com", "17.99", "Sep 25, 2026")])
    build(store, resolver)
    assert only(store, merchant_id="hulu")["next_charge_date"] == "2026-10-25"
    build(store, resolver, today=date(2026, 10, 30))
    assert only(store, merchant_id="hulu")["next_charge_date"] == "2026-11-25"


def test_explicit_renewal_date_beats_computed(store, resolver):
    ingest(store, resolver, [
        receipt("c1", "Costco", "costco.com", "65.00", "Dec 1, 2025", period_word="annual"),
        {"id": "c2", "from": "Costco <billing@costco.com>", "subject": "Your Costco membership will renew soon",
         "body": "Your annual membership will automatically renew on Dec 5, 2026 for $65.00.",
         "date": "2026-10-11T16:00:00Z"}])
    build(store, resolver)
    s = only(store, merchant_id="costco")
    assert s["next_charge_date"] == "2026-12-05" and s["period"] == "annual"


def test_renewal_notice_only(store, resolver):
    ingest(store, resolver, [{"id": "v1", "from": "NordVPN <billing@nordvpn.com>",
                              "subject": "Your NordVPN membership will renew soon",
                              "body": "Your annual membership will automatically renew on Dec 1, 2026 for $99.00.",
                              "date": "2026-10-11T16:00:00Z"}])
    build(store, resolver)
    s = only(store, merchant_id="nordvpn")
    assert s["amount_cents"] == 9900 and s["period"] == "annual" and s["next_charge_date"] == "2026-12-01"
    assert s["last_charge_date"] is None and store.count("charges") == 0


def test_aggregator_split_and_billing_channels(store, resolver):
    ingest(store, resolver, [
        {"id": "a1", "from": "Apple <no_reply@email.apple.com>", "subject": "Your receipt from Apple",
         "body": "Calm Premium (Monthly) $14.99. Thanks for your payment. Billed monthly.", "date": "2026-10-02T16:00:00Z"},
        {"id": "p1", "from": "PayPal <service@paypal.com>", "subject": "Receipt for your payment to Spotify",
         "body": "You sent a payment of $11.99 to Spotify. Thanks for your payment. Billed monthly.",
         "date": "2026-10-04T16:00:00Z"},
        {"id": "s1", "from": "Stripe <receipts@stripe.com>", "subject": "Your receipt from Canva",
         "body": "Canva Pro subscription payment. Amount paid $14.99. Billed monthly.", "date": "2026-10-05T16:00:00Z"},
        {"id": "a2", "from": "Apple <no_reply@email.apple.com>", "subject": "Your receipt from Apple",
         "body": "Some Unknown App Subscription $4.99. Thanks for your payment. Billed monthly.",
         "date": "2026-10-06T16:00:00Z"},
    ])
    build(store, resolver)
    calm = only(store, merchant_id="calm")
    assert calm["billing_channel"] == "apple" and calm["amount_cents"] == 1499
    assert only(store, merchant_id="spotify")["billing_channel"] == "paypal"
    assert only(store, merchant_id="canva")["billing_channel"] == "direct"   # Stripe processes; merchant bills
    unknown = only(store, merchant_id="apple")
    assert unknown["display_name"].endswith("(service not identified)")
    assert unknown["confidence"] < calm["confidence"]


def test_same_merchant_two_accounts_are_separate(store, resolver):
    ingest(store, resolver, [receipt("s1", "Spotify", "spotify.com", "11.99", "Oct 1, 2026")], account="alex@example.com")
    ingest(store, resolver, [receipt("s2", "Spotify", "spotify.com", "11.99", "Oct 2, 2026")], account="sam@example.com")
    build(store, resolver)
    rows = store.find("subscriptions", {"merchant_id": "spotify"})
    assert sorted(r["account_email"] for r in rows) == ["alex@example.com", "sam@example.com"]


def test_trial_then_conversion(store, resolver):
    ingest(store, resolver, [{"id": "t1", "from": "Peacock <hello@peacocktv.com>",
                              "subject": "Welcome to your Peacock free trial",
                              "body": "Your 7-day free trial has started. After your trial ends on Oct 26, 2026 you'll "
                                      "be charged $7.99/month unless you cancel.", "date": "2026-10-19T16:00:00Z"}])
    build(store, resolver)
    s = only(store, merchant_id="peacock")
    assert s["status"] == "trial" and s["is_trial"] == 1 and s["trial_end_date"] == "2026-10-26"
    assert s["trial_converts_to_cents"] == 799 and s["next_charge_date"] == "2026-10-26" and s["period"] == "monthly"
    ingest(store, resolver, [receipt("t2", "Peacock", "peacocktv.com", "7.99", "Oct 26, 2026")])
    build(store, resolver, today=date(2026, 10, 27))
    s = only(store, merchant_id="peacock")
    assert s["status"] == "active" and s["is_trial"] == 0 and s["trial_end_date"] is None


def test_no_charge_for_two_periods_is_expired(store, resolver):
    ingest(store, resolver, [receipt("e1", "Hulu", "hulu.com", "17.99", "May 1, 2026"),
                             receipt("e2", "Hulu", "hulu.com", "17.99", "Jun 1, 2026")])
    build(store, resolver)
    s = only(store, merchant_id="hulu")
    assert s["status"] == "expired" and s["next_charge_date"] is None
    assert sm.list_subscriptions(store)["subscriptions"] == []


def test_last_used_signals_are_inferred(store, resolver):
    ingest(store, resolver, [
        receipt("u1", "Hulu", "hulu.com", "17.99", "Oct 1, 2026"),
        {"id": "u2", "from": "Hulu <hello@hulu.com>", "subject": "We miss you",
         "body": "It's been a while. Come back for new episodes.", "date": "2026-10-10T16:00:00Z"},
        receipt("u3", "Spotify", "spotify.com", "11.99", "Oct 1, 2026"),
        {"id": "u4", "from": "Spotify <no-reply@spotify.com>", "subject": "New sign-in to your Spotify account",
         "body": "We noticed a new sign-in from Chrome on Mac.", "date": "2026-10-12T16:00:00Z"},
        receipt("u5", "Dropbox", "dropbox.com", "11.99", "Oct 1, 2026"),
        {"id": "u6", "from": "Dropbox <no-reply@dropbox.com>", "subject": "New sign-in to Dropbox",
         "body": "A new sign-in to your account.", "date": "2026-06-01T16:00:00Z"},   # too old
        receipt("u7", "Netflix", "netflix.com", "15.49", "Oct 1, 2026"),
    ])
    build(store, resolver)
    hulu, spotify = only(store, merchant_id="hulu"), only(store, merchant_id="spotify")
    assert hulu["last_used_signal"] == "likely_unused" and "Inferred" in hulu["last_used_evidence"]
    assert "We miss you" in hulu["last_used_evidence"] and "2026-10-10" in hulu["last_used_evidence"]
    assert spotify["last_used_signal"] == "active" and spotify["last_used_evidence"].startswith("Inferred")
    for mid in ("dropbox", "netflix"):
        s = only(store, merchant_id=mid)
        assert s["last_used_signal"] == "unknown" and s["last_used_evidence"].startswith("Inferred")
    # the usage emails themselves are not subscriptions
    assert store.count("subscriptions") == 4


def test_price_increase_from_charges(store, resolver):
    ingest(store, resolver, [receipt("p1", "Netflix", "netflix.com", "15.49", "Aug 3, 2026"),
                             receipt("p2", "Netflix", "netflix.com", "17.99", "Sep 3, 2026")])
    out = build(store, resolver)
    (c,) = out["price_changes"]
    assert (c["old_cents"], c["new_cents"], c["increase_cents"], c["kind"]) == (1549, 1799, 250, "charge_increase")
    assert c["evidence_message_ids"] == ["p1", "p2"]
    assert c["alternatives"]["similar_services"] and "not checked" in c["alternatives"]["note"]
    assert "netflix" not in [x.lower() for x in c["alternatives"]["similar_services"]]
    text = notifier.format_price_changes(out["price_changes"])
    assert "$15.49" in text and "$17.99/month" in text


def test_price_increase_from_notice_and_no_alert_on_decrease(store, resolver):
    ingest(store, resolver, [
        receipt("q1", "Spotify", "spotify.com", "11.99", "Oct 1, 2026"),
        {"id": "q2", "from": "Spotify <no-reply@spotify.com>", "subject": "Your Spotify renewal and price change",
         "body": "Your Premium plan price is changing from $11.99 to $12.99 per month. It will renew on Nov 1, 2026.",
         "date": "2026-10-05T16:00:00Z"},
        receipt("q3", "Hulu", "hulu.com", "17.99", "Sep 1, 2026"),
        receipt("q4", "Hulu", "hulu.com", "9.99", "Oct 1, 2026"),
    ])
    out = build(store, resolver)
    assert [(c["display_name"].split()[0], c["kind"], c["new_cents"]) for c in out["price_changes"]] == [
        ("Spotify", "price_change_notice", 1299)]


def test_rebuild_is_idempotent(store, resolver):
    msgs = [receipt("i1", "Netflix", "netflix.com", "15.49", "Sep 3, 2026"),
            receipt("i2", "Netflix", "netflix.com", "15.49", "Oct 3, 2026"),
            receipt("i3", "Hulu", "hulu.com", "17.99", "Oct 5, 2026")]
    ingest(store, resolver, msgs)
    first = build(store, resolver)
    before = {r["id"]: r for r in store.find("subscriptions")}
    charges = store.count("charges")
    ingest(store, resolver, msgs)          # re-ingest is a no-op too
    second = build(store, resolver)
    assert len(first["created"]) == 2 and second["created"] == [] and second["updated"] == []
    assert second["charges_added"] == 0 and store.count("charges") == charges
    assert {r["id"]: r for r in store.find("subscriptions")} == before


@pytest.mark.parametrize("status", sorted(sm.PROTECTED_STATUSES))
def test_rebuild_never_overwrites_protected_status(store, resolver, status):
    ingest(store, resolver, [receipt("k1", "Netflix", "netflix.com", "15.49", "Oct 3, 2026")])
    build(store, resolver)
    s = only(store, merchant_id="netflix")
    store.update("subscriptions", s["id"], {"status": status, "user_decision": "cancel"})
    ingest(store, resolver, [receipt("k2", "Netflix", "netflix.com", "15.49", "Nov 3, 2026")])
    build(store, resolver, today=date(2026, 11, 5))
    s = only(store, merchant_id="netflix")
    assert s["status"] == status and s["user_decision"] == "cancel" and s["last_charge_date"] == "2026-11-03"


def test_unresolved_messages_are_reported(store, resolver):
    ingest(store, resolver, [{"id": "z1", "from": "Billing <billing@obscure-saas.example>", "subject": "Your receipt",
                              "body": "Thanks for your payment. You were charged $5.00 for your monthly plan.",
                              "date": "2026-10-01T16:00:00Z"}])
    out = build(store, resolver)
    assert out["unresolved_message_ids"] == ["z1"] and store.count("subscriptions") == 0


def test_no_full_bodies_or_card_numbers_are_stored(store, resolver):
    ingest(store, resolver, [receipt("r1", "Netflix", "netflix.com", "15.49", "Oct 3, 2026",
                                     extra="Card ending in 4242. " + "Lorem ipsum " * 40)])
    build(store, resolver)
    s = only(store, merchant_id="netflix")
    dump = json.dumps(s) + json.dumps(store.find("charges"))
    assert "Lorem ipsum Lorem ipsum Lorem ipsum" not in dump
    assert store.find("charges")[0]["card_last4"] == "4242"


# --- user operations --------------------------------------------------------------------------------
def test_ignore_and_unignore(store, resolver):
    ingest(store, resolver, [receipt("g1", "Netflix", "netflix.com", "15.49", "Oct 3, 2026")])
    build(store, resolver)
    s = only(store, merchant_id="netflix")
    assert sm.update_subscription(store, s["id"], status="ignored")["status"] == "ignored"
    assert sm.list_subscriptions(store)["subscriptions"] == []
    assert len(sm.list_subscriptions(store, include_inactive=True)["subscriptions"]) == 1
    build(store, resolver)
    assert only(store, merchant_id="netflix")["status"] == "ignored"
    assert sm.update_subscription(store, s["id"], status="active")["status"] == "active"


@pytest.mark.parametrize("kwargs,err", [
    ({"status": "cancelled"}, "confirmation gate"), ({"status": "cancel_requested"}, "confirmation gate"),
    ({"status": "active"}, "only an ignored"), ({"user_decision": "nuke"}, "user_decision"), ({}, "nothing"),
])
def test_update_subscription_rejects(store, resolver, kwargs, err):
    s = sm.add_manual_subscription(store, "Gym", 4000, "monthly")
    with pytest.raises(ValueError, match=err):
        sm.update_subscription(store, s["id"], **kwargs)


def test_update_cancel_decision_does_not_cancel(store):
    s = sm.add_manual_subscription(store, "Gym", 4000, "monthly")
    out = sm.update_subscription(store, s["id"], user_decision="cancel")
    assert out["user_decision"] == "cancel" and out["status"] == "active"
    assert store.count("action_requests") == 0


def test_update_cannot_touch_cancellation_flow_states(store):
    s = sm.add_manual_subscription(store, "Gym", 4000, "monthly")
    store.update("subscriptions", s["id"], {"status": "cancel_in_progress"})
    with pytest.raises(ValueError, match="cancellation flow"):
        sm.update_subscription(store, s["id"], status="ignored")


@pytest.mark.parametrize("kwargs", [
    {"amount_cents": -1}, {"amount_cents": 1.5}, {"amount_cents": True}, {"period": "fortnightly"},
    {"next_charge_date": "next tuesday"}, {"display_name": ""}, {"display_name": "x" * 121},
])
def test_add_manual_validation(store, kwargs):
    args = {"display_name": "Gym", "amount_cents": 4000, "period": "monthly", **kwargs}
    with pytest.raises(ValueError):
        sm.add_manual_subscription(store, **args)


def test_manual_subscription_survives_rebuild_and_merges(store, resolver):
    s = sm.add_manual_subscription(store, "Netflix", 1549, "monthly", merchant_id="netflix", account_email=ACCT)
    assert s["source"] == ["user_added"] and s["monthly_equiv_cents"] == 1549
    ingest(store, resolver, [receipt("mm1", "Netflix", "netflix.com", "15.49", "Oct 3, 2026")])
    build(store, resolver)
    merged = only(store, merchant_id="netflix")
    assert merged["id"] == s["id"] and merged["source"] == ["gmail", "user_added"]


# --- listing ------------------------------------------------------------------------------------------
def test_list_and_text_en_zh(store, resolver):
    ingest(store, resolver, [
        receipt("l1", "Netflix", "netflix.com", "15.49", "Oct 3, 2026", next_date="Nov 3, 2026"),
        receipt("l2", "Dropbox", "dropbox.com", "119.88", "Mar 10, 2026", period_word="annual"),
        {"id": "l3", "from": "Peacock <hello@peacocktv.com>", "subject": "Welcome to your Peacock free trial",
         "body": "Your 7-day free trial has started. After your trial ends on Oct 26, 2026 you'll be charged "
                 "$7.99/month unless you cancel.", "date": "2026-10-19T16:00:00Z"}])
    build(store, resolver)
    listing = sm.list_subscriptions(store)
    names = [s["display_name"] for s in listing["subscriptions"]]
    assert names[0] == "Netflix" and len(names) == 3
    assert listing["monthly_total_cents"] == 1549 + 999      # trial is not counted yet
    en = notifier.format_subscription_list(listing, "en")
    assert "Netflix · $15.49/month · next charge 2026-11-03 · last used: unknown (inferred)" in en
    assert "free trial ends 2026-10-26, then $7.99/month" in en
    assert "Active total: $25.48/month" in en
    zh = notifier.format_subscription_list(listing, "zh")
    assert "推断" in zh and "下次扣费" in zh
    assert notifier.format_subscription_list({"subscriptions": []}) == "I haven't found any subscriptions yet."


# --- synthetic fixture recall (real labelled samples are blocker B1) ---------------------------------
def test_synthetic_fixture_recall(store, resolver):
    rows = [json.loads(line) for line in open(os.path.join(FIXTURES, "samples.jsonl"), encoding="utf-8")]
    ingest(store, resolver, [{k: r[k] for k in ("id", "from", "subject", "body", "date")} for r in rows])
    build(store, resolver)
    found = {(resolver.get(s["merchant_id"]) or {}).get("name") for s in store.find("subscriptions")}
    expected = [r for r in rows if r["category"] in sm.INVENTORY_CATEGORIES]
    hits = [r for r in expected if resolver.resolve(name=r["merchant_name"]) and
            resolver.resolve(name=r["merchant_name"])["name"] in found]
    assert len(hits) / len(expected) >= 0.9


def test_module_has_no_network_or_process_access():
    src = open(sm.__file__, encoding="utf-8").read()
    for banned in ("import socket", "urllib", "http.client", "subprocess", "requests", "smtplib"):
        assert banned not in src
