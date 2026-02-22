from datetime import datetime, timedelta, timezone

import pytest
import requests

from data.xero import XeroClient, XeroError


class _Resp:
    def __init__(self, status_code: int, payload=None, text: str = "", headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")

    def json(self):
        return self._payload


def _token_payload():
    return {
        "tenant_id": "tenant-1",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }


def test_request_retries_on_429_with_backoff(monkeypatch):
    monkeypatch.setattr("data.xero.get_xero_tokens", lambda _sid: _token_payload())

    client = XeroClient("site-1")
    responses = [
        _Resp(429, payload={"error": "rate"}),
        _Resp(429, payload={"error": "rate"}),
        _Resp(200, payload={"ok": True}),
    ]
    sleep_calls = []

    monkeypatch.setattr("data.xero.time.sleep", lambda sec: sleep_calls.append(sec))
    monkeypatch.setattr(client.session, "request", lambda *_a, **_k: responses.pop(0))

    result = client._request("GET", "BankTransactions")

    assert result == {"ok": True}
    assert sleep_calls == [1, 2]


def test_request_retries_on_network_exception(monkeypatch):
    monkeypatch.setattr("data.xero.get_xero_tokens", lambda _sid: _token_payload())

    client = XeroClient("site-1")
    responses = [
        requests.Timeout("timeout"),
        _Resp(200, payload={"ok": True}),
    ]
    sleep_calls = []

    def _request(*_a, **_k):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("data.xero.time.sleep", lambda sec: sleep_calls.append(sec))
    monkeypatch.setattr(client.session, "request", _request)

    result = client._request("GET", "BankTransactions")

    assert result == {"ok": True}
    assert sleep_calls == [1]


def test_request_raises_after_retry_budget_exhausted(monkeypatch):
    monkeypatch.setattr("data.xero.get_xero_tokens", lambda _sid: _token_payload())

    client = XeroClient("site-1")
    responses = [_Resp(429, payload={"error": "rate"}, text="rate limit") for _ in range(5)]
    monkeypatch.setattr("data.xero.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client.session, "request", lambda *_a, **_k: responses.pop(0))

    with pytest.raises(XeroError) as exc:
        client._request("GET", "BankTransactions")

    assert "429" in str(exc.value)
