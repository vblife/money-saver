"""[RISKY] HttpBlandClient: tested only with a stub opener. No network, no real key."""
import io
import json
import os

import pytest

from executors import bland_http
from executors.bland import BlandUnavailableError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_needs_a_key(monkeypatch):
    monkeypatch.delenv(bland_http.API_KEY_ENV, raising=False)
    with pytest.raises(BlandUnavailableError):
        bland_http.HttpBlandClient()


def test_speaks_the_documented_endpoints_and_hides_the_key(monkeypatch):
    monkeypatch.setenv(bland_http.API_KEY_ENV, "test-not-a-key")
    seen = []

    def opener(req, timeout):
        seen.append((req.get_method(), req.full_url, req.get_header("Authorization"),
                     json.loads(req.data) if req.data else None))
        return Resp(json.dumps({"status": "success", "call_id": "c1"}).encode())

    c = bland_http.HttpBlandClient(opener=opener)
    assert "test-not-a-key" not in repr(c)
    assert c.send_call({"phone_number": "+18005550100", "task": "x"})["call_id"] == "c1"
    c.get_call("c1")
    c.stop_call("c1")
    assert seen == [("POST", "https://api.bland.ai/v1/calls", "Bearer test-not-a-key",
                     {"phone_number": "+18005550100", "task": "x"}),
                    ("GET", "https://api.bland.ai/v1/calls/c1", "Bearer test-not-a-key", None),
                    ("POST", "https://api.bland.ai/v1/calls/c1/stop", "Bearer test-not-a-key", {})]


def test_transport_errors_become_bland_unavailable_without_the_key():
    def broken(req, timeout):
        raise OSError("down")
    with pytest.raises(BlandUnavailableError) as exc:
        bland_http.HttpBlandClient(api_key="secret-k", opener=broken).get_call("c1")
    assert "secret-k" not in str(exc.value)


def test_nothing_constructs_it_by_default(tmp_path):
    from server.server import App
    app = App(str(tmp_path / "msb.db"), str(tmp_path / "ev"), [str(tmp_path / "inbox")])
    assert app.bland_client is None
    for d in ("server", "core", "modules", "executors", "routines", "bot"):
        for dirpath, _, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".py") and f != "bland_http.py":
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        src = fh.read()
                    assert "HttpBlandClient" not in src and "bland_http" not in src, f
