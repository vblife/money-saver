"""M1b routines as code (R1, R3, R4, R10): idempotent, never execute anything."""
import os
from datetime import datetime

from routines import r1_daily_scan, r3_reminder_dispatch, r4_trial_guard, r10_expiry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeGmail:
    def __init__(self, messages):
        self.messages, self.queries = messages, []

    def search(self, q):
        self.queries.append(q)
        return list(self.messages) if "free trial" in q or "receipt" in q else []


MSGS = [
    {"id": "m1", "from": "Netflix <billing@netflix.com>", "subject": "Your Netflix receipt", "date": "2026-10-03T16:00:00Z",
     "body": "Thanks for your payment. You were charged $15.49 for your monthly Netflix plan on Oct 3, 2026."},
    {"id": "m2", "from": "Peacock <hello@peacocktv.com>", "subject": "Welcome to your Peacock free trial",
     "date": "2026-10-19T16:00:00Z",
     "body": "Your 7-day free trial has started. After your trial ends on Oct 26, 2026 you'll be charged $7.99/month."},
]


def test_r1_scan_inventory_reminders_idempotent(store, gate, clock):
    gm = FakeGmail(MSGS)
    out = r1_daily_scan.run(store, gate, gm, ["alex@example.com"])
    assert out["scans"]["alex@example.com"]["ingested"] == 2
    assert len(out["inventory"]["created"]) == 2 and len(out["reminders"]["scheduled"]) == 2
    counts = {t: store.count(t) for t in ("subscriptions", "charges", "reminders", "source_messages")}
    again = r1_daily_scan.run(store, gate, gm, ["alex@example.com"])
    assert again["inventory"]["created"] == [] and again["reminders"]["scheduled"] == []
    assert {t: store.count(t) for t in counts} == counts
    assert all("after:" in q for q in gm.queries)
    assert store.get_user()["scan_cursor"]["alex@example.com"]


def test_r3_r4_r10_flow_never_executes(store, gate, clock):
    r1_daily_scan.run(store, gate, FakeGmail(MSGS), ["alex@example.com"])
    clock.now = datetime.fromisoformat("2026-10-24T16:00:00+00:00")
    assert len(r3_reminder_dispatch.run(store, gate)["messages"]) == 1
    out = r4_trial_guard.run(store, gate)
    assert out["ready_to_execute"] == [] and out["reminders_due"]["messages"] == []
    assert r10_expiry.run(store, gate) == {"expired": [], "messages": []}
    assert store.count("action_runs") == 0 and store.count("action_requests") == 0


def test_routines_have_no_side_channels():
    for name in ("r1_daily_scan", "r3_reminder_dispatch", "r4_trial_guard", "r10_expiry"):
        src = open(os.path.join(ROOT, "routines", f"{name}.py"), encoding="utf-8").read()
        for banned in ("socket", "urllib", "http.client", "subprocess", "smtplib", "begin_execution", "import requests"):
            assert banned not in src, (name, banned)
    for mod in ("trials", "duplicates"):
        src = open(os.path.join(ROOT, "modules", f"{mod}.py"), encoding="utf-8").read()
        for banned in ("socket", "urllib", "subprocess", "smtplib", "begin_execution", "finish_execution"):
            assert banned not in src, (mod, banned)
