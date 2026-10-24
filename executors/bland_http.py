"""HTTP client for the Bland v1 call endpoints (B3, D38a).

This is the only code that reads the Bland API key (``BLAND_API_KEY``) and sends it to Bland. Nothing constructs it
by default: ``server.App`` has ``bland_client=None`` unless a client is passed in, every live call is still behind
the live switch, Charles's first-paid-call approval (D35), a reviewed number and the call limits (D38), and tests use
a stub opener only.

Endpoints (https://docs.bland.ai): ``POST /v1/calls``, ``GET /v1/calls/{call_id}``, ``POST /v1/calls/{call_id}/stop``,
with the key sent as a Bearer token in the ``authorization`` header.

Hardening (D38a):

* The base URL is fixed to ``https://api.bland.ai/v1``. There is no constructor argument, attribute setter, config
  file or environment variable that changes it, so the key can only ever go to Bland.
* ``POST /v1/calls`` may already have queued a paid call when the reply times out, cannot be read, or is a server
  error (5xx). Those raise :class:`CallStatusUnknownError`, never :class:`BlandUnavailableError`, so the executor
  keeps the run open as ``unknown`` and a retry cannot place a second call. The same goes for any other transport
  failure on that request. Only failures before anything was sent (DNS, connection refused, TLS setup) or a
  rejection by Bland (4xx) raise ``BlandUnavailableError``.
* An HTTP error's response body is kept on the exception (``.status``, ``.body``; at most 2000 characters) with the
  key removed.

TODO(B3, Charles): where the key lives in deploy config, and the dispatch choice (plain ``task`` or a Bland agent,
persona or pathway).
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

from executors.bland import BlandUnavailableError, CallStatusUnknownError

API_BASE = "https://api.bland.ai/v1"
API_KEY_ENV = "BLAND_API_KEY"
BODY_MAX = 2000


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, (TimeoutError, socket.timeout))


def _never_sent(exc: BaseException) -> bool:
    """Only failures that happen before the request leaves this machine: DNS, connection refused, TLS setup."""
    reason = getattr(exc, "reason", exc) if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLError))


class BlandHttpError(BlandUnavailableError):
    """Bland answered with an HTTP error. ``status`` and the (key-free) ``body`` are kept for debugging."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status, self.body = status, body


class HttpBlandClient:
    """Implements ``executors.bland.CallClient``. The base URL is fixed (see the module docstring)."""

    __slots__ = ("_key", "timeout", "_open")

    def __init__(self, api_key: str | None = None, timeout: float = 20.0,
                 opener: Callable[..., Any] = urllib.request.urlopen):
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise BlandUnavailableError(f"no Bland API key ({API_KEY_ENV} is not set)")
        self._key, self.timeout, self._open = key, timeout, opener

    @property
    def base_url(self) -> str:
        return API_BASE

    def __repr__(self) -> str:  # never show the key
        return f"HttpBlandClient(base_url={API_BASE!r})"

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[redacted]")[:BODY_MAX]

    def _http_error(self, exc: urllib.error.HTTPError) -> tuple[str, str]:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - the body is only for debugging
            body = ""
        body = self._scrub(body)
        return f"Bland returned HTTP {exc.code}" + (f": {body[:300]}" if body else ""), body

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None,
                 places_call: bool = False) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method,
                                     headers={"authorization": f"Bearer {self._key}",
                                              "content-type": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            message, text = self._http_error(exc)
            if places_call and exc.code >= 500:
                err = CallStatusUnknownError(f"{message}; the call may have been queued")
                err.status, err.body = exc.code, text  # type: ignore[attr-defined]
                raise err from None
            raise BlandHttpError(message, exc.code, text) from None
        except Exception as exc:  # noqa: BLE001 - transport problems
            if places_call and _is_timeout(exc):
                raise CallStatusUnknownError(f"POST {path} timed out; the call may have been queued") from None
            if places_call and not _never_sent(exc):
                # e.g. the connection dropped while sending or before the reply: Bland may have taken the call
                raise CallStatusUnknownError(f"POST {path} failed ({type(exc).__name__}); the call may have been "
                                             "queued") from None
            raise BlandUnavailableError(f"Bland request failed: {type(exc).__name__}") from None
        try:
            parsed = json.loads(raw.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if not isinstance(parsed, dict):
            if places_call:
                raise CallStatusUnknownError(f"unreadable reply to POST {path}: "
                                             f"{self._scrub(raw.decode('utf-8', 'replace'))[:300]!r}")
            raise BlandUnavailableError(f"unreadable reply to {method} {path}")
        return parsed

    def send_call(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/calls", payload, places_call=True)

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/calls/{call_id}")

    def stop_call(self, call_id: str) -> dict[str, Any]:
        return self._request("POST", f"/calls/{call_id}/stop", {})
