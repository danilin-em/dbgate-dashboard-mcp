"""Tests for the DbGate HTTP client — httpx stubbed, no network."""

import asyncio
from types import SimpleNamespace

import pytest

import dbgate_dashboard_mcp.dbgate as dbgate


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    """Replays a scripted list of responses and records every request."""

    def __init__(self, script, calls):
        self._script = script
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self._calls.append({"url": url, "json": json, "headers": headers})
        return self._script.pop(0)


@pytest.fixture
def http(monkeypatch):
    """Stub httpx.AsyncClient; `http.script` is the response queue."""
    script, calls = [], []
    monkeypatch.setattr(
        dbgate.httpx, "AsyncClient", lambda *a, **k: FakeClient(script, calls)
    )
    monkeypatch.delenv("DBGATE_LOGIN", raising=False)
    monkeypatch.delenv("DBGATE_PASSWORD", raising=False)
    monkeypatch.delenv("DBGATE_URL", raising=False)
    dbgate.reset_token()
    return SimpleNamespace(script=script, calls=calls)


def _login_ok(token="tok1"):
    return FakeResponse(payload={"accessToken": token})


def test_call_logs_in_then_sends_bearer(http):
    http.script += [_login_ok(), FakeResponse(payload=[{"_id": "con1"}])]

    out = asyncio.run(dbgate.call("connections/list"))

    assert out == [{"_id": "con1"}]
    assert http.calls[0]["url"] == "http://localhost:3000/auth/login"
    assert http.calls[0]["json"] == {"amoid": "none"}
    assert http.calls[1]["url"] == "http://localhost:3000/connections/list"
    assert http.calls[1]["json"] == {}
    assert http.calls[1]["headers"]["Authorization"] == "Bearer tok1"


def test_token_is_reused_across_calls(http):
    http.script += [
        _login_ok(),
        FakeResponse(payload={"rows": []}),
        FakeResponse(payload={"rows": [1]}),
    ]

    asyncio.run(dbgate.call("database-connections/query-data", {"sql": "select 1"}))
    out = asyncio.run(dbgate.call("database-connections/query-data", {"sql": "select 2"}))

    assert out == {"rows": [1]}
    logins = [c for c in http.calls if c["url"].endswith("/auth/login")]
    assert len(logins) == 1  # second call reused the cached token


def test_base_url_from_env_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("DBGATE_URL", "http://dbgate.internal:3000/")
    assert dbgate.base_url() == "http://dbgate.internal:3000"


def test_base_url_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("DBGATE_URL", raising=False)
    assert dbgate.base_url() == "http://localhost:3000"
