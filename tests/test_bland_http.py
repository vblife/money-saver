"""HttpBlandClient (D38a): tested only with a stub opener. No network, no real key."""
import http.client
import inspect
import io
import json
import os
import socket
import urllib.error

import pytest

from executors import bland_http
from core.action_gate import GateError
from executors import bland
from executors.bland import BlandUnavailableError, CallStatusUnknownError
from test_executors import call_request, live  # noqa: F401 - fixture

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


# -- D38a: the base URL is fixed ---------------------------------------------------------------------------------
def test_base_url_is_locked_to_bland(monkeypatch):
    assert bland_http.API_BASE == "https://api.bland.ai/v1"
    assert "base_url" not in inspect.signature(bland_http.HttpBlandClient).parameters
    with pytest.raises(TypeError):
        bland_http.HttpBlandClient(api_key="k", base_url="https://evil.example")  # noqa
    c = bland_http.HttpBlandClient(api_key="k", opener=lambda req, timeout: Resp(b'{"status": "success"}'))
    with pytest.raises(AttributeError):
        c.base_url = "https://evil.example"
    with pytest.raises(AttributeError):
        c._base = "https://evil.example"  # no other attribute can be added (__slots__)
    for var in ("BLAND_BASE_URL", "BLAND_API_BASE", "BLAND_URL"):
        monkeypatch.setenv(var, "https://evil.example")
    seen = []
    c = bland_http.HttpBlandClient(api_key="k", opener=lambda req, timeout: seen.append(req.full_url) or
                                   Resp(b'{"status": "success", "call_id": "c1"}'))
    c.send_call({"phone_number": "+18005550100", "task": "x"})
    c.get_call("c1")
    c.stop_call("c1")
    assert c.base_url == "https://api.bland.ai/v1"
    assert seen and all(u.startswith("https://api.bland.ai/v1/") for u in seen)
    with open(os.path.join(ROOT, "executors", "bland_http.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "BASE_URL" not in src and "environ.get(\"BLAND_B" not in src


# -- D38a: a POST /calls that may have gone through is "unknown", never "failed" -------------------------------
def raising(exc):
    def opener(req, timeout):
        raise exc
    return opener


def replying(raw):
    return lambda req, timeout: Resp(raw)


@pytest.mark.parametrize("opener", [
    raising(socket.timeout("timed out")), raising(TimeoutError()), raising(urllib.error.URLError(TimeoutError())),
    raising(http.client.RemoteDisconnected("closed")), raising(ConnectionResetError()),
    replying(b"<html>Bad gateway</html>"), replying(b"[1, 2]"), replying(b"\xff\xfe"), replying(b'"queued"'),
])
def test_send_call_timeouts_and_unreadable_replies_are_unknown(opener):
    with pytest.raises(CallStatusUnknownError) as exc:
        bland_http.HttpBlandClient(api_key="secret-k", opener=opener).send_call({"task": "x"})
    assert "secret-k" not in str(exc.value)


@pytest.mark.parametrize("opener", [
    raising(urllib.error.URLError(ConnectionRefusedError())), raising(urllib.error.URLError(socket.gaierror())),
])
def test_send_call_failures_before_sending_are_unavailable(opener):
    with pytest.raises(BlandUnavailableError):
        bland_http.HttpBlandClient(api_key="k", opener=opener).send_call({"task": "x"})


@pytest.mark.parametrize("opener", [raising(socket.timeout()), replying(b"<html>")])
def test_reads_that_fail_are_just_unavailable(opener):
    c = bland_http.HttpBlandClient(api_key="k", opener=opener)
    with pytest.raises(BlandUnavailableError):
        c.get_call("c1")
    with pytest.raises(BlandUnavailableError):
        c.stop_call("c1")


def http_error(code, body):
    return urllib.error.HTTPError("https://api.bland.ai/v1/calls", code, "err", {}, io.BytesIO(body))


def test_http_error_bodies_are_kept_without_the_key():
    body = b'{"status": "error", "message": "Insufficient balance", "echo": "Bearer secret-k"}'
    with pytest.raises(bland_http.BlandHttpError) as exc:
        bland_http.HttpBlandClient(api_key="secret-k", opener=raising(http_error(402, body))).send_call({})
    e = exc.value
    assert e.status == 402 and "Insufficient balance" in e.body and "Insufficient balance" in str(e)
    assert "secret-k" not in e.body and "secret-k" not in str(e) and "[redacted]" in e.body
    big = b"x" * 5000
    with pytest.raises(bland_http.BlandHttpError) as exc:
        bland_http.HttpBlandClient(api_key="k", opener=raising(http_error(404, big))).get_call("c1")
    assert len(exc.value.body) == bland_http.BODY_MAX


def test_server_error_on_send_is_unknown_and_keeps_the_body():
    with pytest.raises(CallStatusUnknownError) as exc:
        bland_http.HttpBlandClient(api_key="secret-k", opener=raising(http_error(502, b"upstream secret-k"))
                                   ).send_call({})
    assert exc.value.status == 502 and exc.value.body == "upstream [redacted]"


# -- the executor keeps an unknown call open so no retry can dial again ------------------------------------------
@pytest.mark.parametrize("client", [
    lambda: bland.FakeBlandClient(unknown_send=True),
    lambda: bland.FakeBlandClient(send_response={"status": "success"}),  # no call_id
    lambda: bland.FakeBlandClient(send_response="queued"),
    lambda: bland_http.HttpBlandClient(api_key="k", opener=raising(socket.timeout())),
    lambda: bland_http.HttpBlandClient(api_key="k", opener=replying(b"<html>")),
])
def test_unknown_send_keeps_the_run_open_and_blocks_retries(store, clock, live, client):  # noqa: F811
    guard, gate, ev = live
    c = client()
    ex = bland.BlandCallExecutor(gate, c, ev, clock)
    req = call_request(gate, store, guard)
    out = ex.place(req["id"])
    assert out["status"] == "unknown" and out["needs_verification"] and "Do not place" in out["next_step"]
    run = store.get("action_runs", out["run_id"])
    assert run["finished_at"] is None and run["outcome"] is None
    assert run["log"][-1]["bland_call_status"] == "unknown" and run["log"][-1]["external_id"] == run["id"]
    assert gate.get(req["id"])["status"] != "failed"
    with pytest.raises(GateError, match="Do not place"):  # the same request cannot be placed again
        ex.place(req["id"])
    other = call_request(gate, store, guard)
    with pytest.raises(bland.CallLimitError, match="one call at a time"):
        ex.place(other["id"])
    assert ex.poll(run["id"])["status"] == "unknown"
    with pytest.raises(GateError, match="no confirmed Bland call id"):
        ex.stop(run["id"])
    if isinstance(c, bland.FakeBlandClient):
        assert len(c.sent) == 1
    # a person checks Bland's call log and closes the run; then calls can start again
    gate.finish_execution(run["id"], "failed", error_code="call_not_found_in_bland_log")
    assert ex.place(other["id"])["status"] in ("queued", "unknown")


def test_definite_rejection_still_fails_the_run(store, clock, live):  # noqa: F811
    guard, gate, ev = live
    req = call_request(gate, store, guard)
    fake = bland.FakeBlandClient(send_response={"status": "error", "message": "Number is on the DNC list",
                                                "errors": ["dnc"]})
    out = bland.BlandCallExecutor(gate, fake, ev, clock).place(req["id"])
    assert out["status"] == "failed" and out["error_code"] == "bland_unavailable"
    assert "DNC" in out["run"]["log"][-1]["error"]
    req2 = call_request(gate, store, guard)
    http = bland_http.HttpBlandClient(api_key="k", opener=raising(http_error(402, b'{"message": "balance"}')))
    out = bland.BlandCallExecutor(gate, http, ev, clock).place(req2["id"])
    assert out["status"] == "failed" and "HTTP 402" in out["run"]["log"][-1]["error"]
