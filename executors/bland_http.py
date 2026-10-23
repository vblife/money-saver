"""[RISKY, needs Charles's approval] HTTP client for the Bland v1 call endpoints (B3).

This is the only code that reads the Bland API key (``BLAND_API_KEY``) and sends it to api.bland.ai. Nothing
constructs it by default: ``server.App`` has ``bland_client=None`` unless a client is passed in, every live call is
still behind the live switch, Charles's first-paid-call approval (D35), a reviewed number and the call limits (D38),
and tests use a stub opener only.

Endpoints (https://docs.bland.ai): ``POST /v1/calls``, ``GET /v1/calls/{call_id}``, ``POST /v1/calls/{call_id}/stop``,
header ``authorization: <key>`` sent as a Bearer token.

TODO(B3, Charles): where the key lives in deploy config, and the dispatch choice (plain ``task`` or a Bland agent,
persona or pathway).
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable, Mapping

from executors.bland import BlandUnavailableError

API_BASE = "https://api.bland.ai/v1"
API_KEY_ENV = "BLAND_API_KEY"


class HttpBlandClient:
    """Implements ``executors.bland.CallClient``. Construct it only after Charles approves (B3)."""

    def __init__(self, api_key: str | None = None, base_url: str = API_BASE, timeout: float = 20.0,
                 opener: Callable[..., Any] = urllib.request.urlopen):
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise BlandUnavailableError(f"no Bland API key ({API_KEY_ENV} is not set)")
        self._key, self.base_url, self.timeout, self._open = key, base_url.rstrip("/"), timeout, opener

    def __repr__(self) -> str:  # never show the key
        return f"HttpBlandClient(base_url={self.base_url!r})"

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method,
                                     headers={"authorization": f"Bearer {self._key}",
                                              "content-type": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except Exception as exc:  # noqa: BLE001 - any transport problem means "Bland unavailable"
            raise BlandUnavailableError(f"Bland request failed: {type(exc).__name__}") from exc

    def send_call(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/calls", payload)

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/calls/{call_id}")

    def stop_call(self, call_id: str) -> dict[str, Any]:
        return self._request("POST", f"/calls/{call_id}/stop", {})
