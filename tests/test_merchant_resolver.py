from core.merchant_resolver import MerchantResolver, normalize_name

R = MerchantResolver()


def test_registry_has_top_50_merchants_plus_aggregators():
    non_agg = [m for m in R.merchants.values() if m["category"] != "aggregator"]
    assert len(non_agg) >= 50
    assert {"apple", "google_play", "paypal", "stripe"} <= set(R.merchants)


def test_registry_entries_are_complete_and_marked_unverified():
    required = {"id", "name", "aliases", "email_domains", "category", "cancel_method", "cancel_url", "support_phone",
                "cancel_notes", "retention_offer_known", "price_adjust_policy", "return_policy", "updated_at", "source_urls"}
    for m in R.merchants.values():
        assert required <= set(m), m["id"]
        assert m["verification"] == "unverified"  # Q11: nothing is trusted until checked
        assert m["support_phone"] is None          # never ship unverified phone numbers
        assert m["cancel_method"] in {"web", "phone", "email", "chat", "app_store", "google_play"}
        if m["cancel_url"]:
            assert m["cancel_url"].startswith("https://")


def test_normalize_name():
    assert normalize_name("Netflix, Inc.") == "netflix"
    assert normalize_name("Spotify AB") == "spotify"


def test_spec_example_aliases():
    for raw in ("netflix.com", "Netflix Inc", "NFLX", "Netflix"):
        assert R.by_name(raw) == "netflix", raw
    assert R.resolve(from_addr="Netflix <info@mailer.netflix.com>")["merchant_id"] == "netflix"


def test_aggregator_split():
    hit = R.resolve(from_addr="no_reply@email.apple.com", text="Receipt: Calm Premium (Annual) $69.99")
    assert hit["merchant_id"] == "calm" and hit["billed_via"] == "apple" and hit["method"] == "aggregator_split"
    assert R.resolve(from_addr="no_reply@email.apple.com", text="Receipt: iCloud+ 200GB $2.99")["merchant_id"] == "icloud"
    assert R.resolve(from_addr="no_reply@email.apple.com", text="Receipt")["merchant_id"] == "apple"


def test_unknown_merchant_returns_none_and_runtime_add_is_unverified():
    r = MerchantResolver()
    assert r.resolve(name="Obscure Yoga Studio") is None
    r.add_runtime("obscure_yoga", "Obscure Yoga Studio", "gym", ["obscureyoga.example"])
    hit = r.resolve(from_addr="hi@obscureyoga.example")
    assert hit["merchant_id"] == "obscure_yoga" and hit["verification"] == "unverified"
