"""M1b duplicates (FR-1-6 a/b/c, 10.1)."""
import json
from datetime import date

import pytest

from core import notifier
from core.classifier import Classifier
from core.gmail_scanner import ingest_messages
from core.merchant_resolver import MerchantResolver
from modules import duplicates
from modules.subscriptions import SubscriptionInventory, update_subscription

TODAY = date(2026, 10, 19)


@pytest.fixture(scope="module")
def resolver():
    return MerchantResolver()


def rec(mid, merchant, domain, amount, day="Oct 3, 2026", iso="2026-10-03T16:00:00Z", frm=None, to=None, last4=None):
    body = f"Thanks for your payment. You were charged ${amount} for your monthly {merchant} plan on {day}."
    if last4:
        body += f" Card ending in {last4}."
    m = {"id": mid, "from": frm or f"{merchant} <billing@{domain}>", "subject": f"Your {merchant} receipt",
         "body": body, "date": iso}
    if to:
        m["to"] = to
    return m


def setup(store, resolver, by_account):
    for account, msgs in by_account.items():
        ingest_messages(store, account, msgs, Classifier(resolver))
    SubscriptionInventory(store, resolver).build(TODAY)


def test_same_merchant_on_two_accounts(store, resolver):
    setup(store, resolver, {"alex@example.com": [rec("a", "Spotify", "spotify.com", "11.99", last4="1111")],
                            "alex.work@example.com": [rec("b", "Spotify", "spotify.com", "11.99", last4="2222")]})
    out = duplicates.detect(store, resolver)
    (g,) = out["duplicate_groups"]
    assert g["rule"] == "a" and g["kind"] == "same_merchant" and not g["suspected_same_subscription"]
    assert g["card_endings"] == ["1111", "2222"] and g["current_monthly_total_cents"] == 2398
    assert g["possible_monthly_saving_cents"] is None and "not checked the family plan price" in g["advice"]
    ids = {s["duplicate_group_id"] for s in store.find("subscriptions")}
    assert ids == {g["duplicate_group_id"]} and g["duplicate_group_id"].startswith("dup_")
    again = duplicates.detect(store, resolver)
    assert again["changed_subscription_ids"] == [] and again["duplicate_groups"][0]["duplicate_group_id"] == g["duplicate_group_id"]


def test_same_account_two_channels_is_suspected_same(store, resolver):
    setup(store, resolver, {"alex@example.com": [
        rec("d", "Calm", "calm.com", "14.99"),
        {"id": "e", "from": "Apple <no_reply@email.apple.com>", "subject": "Your receipt from Apple",
         "body": "Calm Premium (Monthly) $14.99. Thanks for your payment. Billed monthly.", "date": "2026-10-04T16:00:00Z"}]})
    (g,) = duplicates.detect(store, resolver)["duplicate_groups"]
    assert g["suspected_same_subscription"] is True and "apple" in g["advice"] and "direct" in g["advice"]
    assert g["possible_monthly_saving_cents"] == 1499
    assert len(store.find("subscriptions")) == 2   # both kept for the user to confirm (10.1)


def test_household_rule_c_and_only_household_recipient_stored(store, resolver):
    store.update_user({"household_emails": ["sam@example.com"]})
    setup(store, resolver, {"alex@example.com": [
        rec("f", "Netflix", "netflix.com", "15.49"),
        rec("g", "Netflix", "netflix.com", "15.49", iso="2026-10-05T16:00:00Z", to="Sam <sam@example.com>"),
        rec("h", "Hulu", "hulu.com", "17.99", to="Alex <alex@example.com>, stranger@example.org")]})
    out = duplicates.detect(store, resolver)
    (g,) = out["duplicate_groups"]
    assert g["rule"] == "c" and g["kind"] == "household" and g["accounts"] == ["alex@example.com", "sam@example.com"]
    rows = {r["message_id"]: r for r in store.find("source_messages")}
    assert rows["g"]["extracted"]["household_recipient"] == "sam@example.com"
    assert "household_recipient" not in rows["h"]["extracted"]
    assert "stranger" not in json.dumps(list(rows.values()))


@pytest.mark.parametrize("services,expected", [
    ([("Netflix", "netflix.com"), ("Hulu", "hulu.com")], []),
    ([("Netflix", "netflix.com"), ("Hulu", "hulu.com"), ("Peacock", "peacocktv.com")], ["streaming_video"]),
    ([("Dropbox", "dropbox.com"), ("Netflix", "netflix.com")], []),
])
def test_overlap_rule_b(store, resolver, services, expected):
    msgs = [rec(f"o{i}", name, dom, "9.99") for i, (name, dom) in enumerate(services)]
    setup(store, resolver, {"alex@example.com": msgs})
    out = duplicates.detect(store, resolver)
    assert [o["category"] for o in out["overlaps"]] == expected
    assert out["duplicate_groups"] == []
    if expected:
        assert out["overlaps"][0]["monthly_total_cents"] == 999 * 3


def test_inactive_rows_do_not_count_and_group_is_cleared(store, resolver):
    setup(store, resolver, {"alex@example.com": [rec("p", "Spotify", "spotify.com", "11.99")],
                            "alex.work@example.com": [rec("q", "Spotify", "spotify.com", "11.99")]})
    duplicates.detect(store, resolver)
    sub = store.find("subscriptions")[0]
    update_subscription(store, sub["id"], status="ignored")
    out = duplicates.detect(store, resolver)
    assert out["duplicate_groups"] == [] and len(out["changed_subscription_ids"]) == 2
    assert all(s["duplicate_group_id"] is None for s in store.find("subscriptions"))


def test_unidentified_aggregator_rows_are_not_grouped(store, resolver):
    msgs = [{"id": f"u{i}", "from": "Apple <no_reply@email.apple.com>", "subject": "Your receipt from Apple",
             "body": f"Mystery App {i} $4.99. Thanks for your payment. Billed monthly.", "date": "2026-10-04T16:00:00Z"}
            for i in range(2)]
    setup(store, resolver, {"alex@example.com": msgs[:1], "other@example.com": msgs[1:]})
    assert duplicates.detect(store, resolver)["duplicate_groups"] == []


def test_format_duplicates(store, resolver):
    setup(store, resolver, {"alex@example.com": [rec("r", "Spotify", "spotify.com", "11.99")],
                            "alex.work@example.com": [rec("s", "Spotify", "spotify.com", "11.99")]})
    text = notifier.format_duplicates(duplicates.detect(store, resolver))
    assert "$23.98/month in total" in text
    assert notifier.format_duplicates({"duplicate_groups": [], "overlaps": []}) == "No duplicate subscriptions found."
