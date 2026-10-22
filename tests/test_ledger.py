import pytest

from core.ledger import Ledger, LedgerError


@pytest.fixture
def ledger(store):
    return Ledger(store)


def add_cancel(ledger, **kw):
    args = dict(module="subscriptions", saving_type="recurring", description="Cancel Hulu", amount_cents=1799,
                source_ref="sub_hulu", dedupe_key="cancel:sub_hulu", occurred_at="2026-10-10", merchant_id="hulu")
    args.update(kw)
    return ledger.add(**args)


def test_add_starts_pending_and_annualizes(ledger):
    e = add_cancel(ledger)
    assert e["status"] == "pending" and e["annualized_cents"] == 1799 * 12 and e["recurring_months"] == 12


def test_dedupe_key_makes_add_idempotent(ledger, store):
    a = add_cancel(ledger)
    b = add_cancel(ledger, amount_cents=9999)
    assert a["id"] == b["id"] and store.count("savings_ledger") == 1


def test_cannot_add_verified_directly_or_negative(ledger):
    with pytest.raises(LedgerError):
        add_cancel(ledger, status="verified")
    with pytest.raises(LedgerError):
        add_cancel(ledger, amount_cents=-5, dedupe_key="neg")


def test_verify_requires_reference_and_is_immutable(ledger):
    e = add_cancel(ledger)
    with pytest.raises(LedgerError):
        ledger.verify(e["id"], "")
    v = ledger.verify(e["id"], "gmail:cancel-confirm-1")
    assert v["status"] == "verified" and v["verified_at"]
    assert ledger.verify(e["id"], "gmail:other")["verification_ref"] == "gmail:cancel-confirm-1"
    with pytest.raises(LedgerError):
        ledger.correct_verified_amount(e["id"], 1)


def test_estimated_cannot_be_verified(ledger):
    e = add_cancel(ledger, status="estimated", dedupe_key="est")
    with pytest.raises(LedgerError):
        ledger.verify(e["id"], "x")


def test_month_summary_separates_statuses_and_forms(ledger):
    v = add_cancel(ledger)
    ledger.verify(v["id"], "gmail:1")
    add_cancel(ledger, description="Cancel Peacock", amount_cents=799, dedupe_key="cancel:peacock", source_ref="sub_p")
    add_cancel(ledger, description="Trial would convert", amount_cents=1000, dedupe_key="est:x", status="estimated")
    pa = ledger.add(module="post_purchase", saving_type="one_time", description="Target price adjustment",
                    amount_cents=2200, source_ref="clm_1", dedupe_key="padj:clm_1", occurred_at="2026-10-12")
    ledger.verify(pa["id"], "gmail:refund-1")
    ledger.add(module="travel", saving_type="one_time", description="Southwest credit", amount_cents=4800,
               source_ref="bkg_1", dedupe_key="tc:bkg_1", occurred_at="2026-10-05", form="travel_credit")
    ledger.add(module="travel", saving_type="one_time", description="Miles", amount_cents=0, points_amount=5000,
               source_ref="bkg_2", dedupe_key="pts:bkg_2", occurred_at="2026-10-05", form="points")
    s = ledger.month_summary(2026, 10)
    assert s["verified_cents"] == 1799 + 2200
    assert s["pending_cents"] == 799
    assert s["estimated_cents"] == 1000
    assert s["travel_credit_cents"] == 4800 and s["points"] == 5000
    assert s["by_module"] == {"subscriptions": 1799, "post_purchase": 2200}


def test_recurring_counts_at_most_12_months(ledger):
    e = add_cancel(ledger)
    ledger.verify(e["id"], "gmail:1")
    assert ledger.month_summary(2027, 9)["verified_cents"] == 1799   # month 12
    assert ledger.month_summary(2027, 10)["verified_cents"] == 0     # month 13
    assert ledger.month_summary(2026, 9)["verified_cents"] == 0      # before


def test_negotiation_promo_months(ledger):
    e = ledger.add(module="bills", saving_type="recurring", description="Xfinity 89->60", amount_cents=2900,
                   source_ref="bil_1", dedupe_key="neg:bil_1", occurred_at="2026-10-01", recurring_months=6)
    ledger.verify(e["id"], "gmail:new-bill")
    assert ledger.month_summary(2027, 3)["verified_cents"] == 2900
    assert ledger.month_summary(2027, 4)["verified_cents"] == 0


def test_reverse_offsets_without_editing(ledger, store):
    e = add_cancel(ledger)
    ledger.verify(e["id"], "gmail:1")
    r = ledger.reverse(e["id"], "charged after cancel")
    assert r["status"] == "reversed" and r["reverses_id"] == e["id"]
    assert store.get("savings_ledger", e["id"])["status"] == "verified"
    assert ledger.month_summary(2026, 10)["verified_cents"] == 0
    assert ledger.reverse(e["id"], "again")["id"] == r["id"]
