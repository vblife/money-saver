import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.action_gate import ActionGate  # noqa: E402
from core.execution_policy import LiveGuard  # noqa: E402
from core.store import Store  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "emails")


class Clock:
    def __init__(self, start=datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc)):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "msb.db"))
    yield s
    s.close()


@pytest.fixture
def clock():
    return Clock()


class GateMechanicsGuard(LiveGuard):
    """Test-only live guard for tests of the confirmation-gate mechanics (approval, expiry, binding, runs).

    It skips the live-execution gates (D33-D36) so those tests can start runs on any channel; starting a run only
    writes rows to the test database. The live gates themselves are tested with the real ``LiveGuard`` in
    tests/test_execution_policy.py, and the server and the default ``ActionGate`` always use the real one."""

    def check(self, store, req):
        return None


@pytest.fixture
def gate(store, clock):
    store.update_user({"approved_pii_fields": ["name", "account_last4", "service_zip"]})
    return ActionGate(store, now_fn=clock, live_guard=GateMechanicsGuard())


def load_jsonl(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.fixture
def samples():
    return load_jsonl("samples.jsonl")


@pytest.fixture
def holdout():
    return load_jsonl("holdout.jsonl")
