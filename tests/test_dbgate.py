"""Tests for the DbGate HTTP client — httpx stubbed, no network."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

import dbgate_dashboard_mcp.dbgate as dbgate
from dbgate_dashboard_mcp.transport import TunneledTransport


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
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def http(monkeypatch):
    """Stub httpx.AsyncClient; `http.script` is the response queue."""
    script, calls, opts = [], [], []

    def _fake_client(*a, **k):
        opts.append(k)
        return FakeClient(script, calls)

    monkeypatch.setattr(dbgate.httpx, "AsyncClient", _fake_client)
    monkeypatch.delenv("DBGATE_LOGIN", raising=False)
    monkeypatch.delenv("DBGATE_PASSWORD", raising=False)
    monkeypatch.delenv("DBGATE_URL", raising=False)
    dbgate.reset_token()
    dbgate.reset_tunnel()
    return SimpleNamespace(script=script, calls=calls, opts=opts)


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


def test_login_sends_credentials_when_env_set(http, monkeypatch):
    monkeypatch.setenv("DBGATE_LOGIN", "admin")
    monkeypatch.setenv("DBGATE_PASSWORD", "s3cret")
    http.script += [_login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))

    assert http.calls[0]["json"] == {
        "amoid": "none",
        "login": "admin",
        "password": "s3cret",
    }


def test_login_password_defaults_to_empty(http, monkeypatch):
    monkeypatch.setenv("DBGATE_LOGIN", "admin")
    monkeypatch.delenv("DBGATE_PASSWORD", raising=False)
    http.script += [_login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))

    assert http.calls[0]["json"]["password"] == ""


def test_login_http_error_raises(http):
    http.script += [FakeResponse(status_code=500, payload={})]

    with pytest.raises(dbgate.DbGateError, match="login failed: HTTP 500"):
        asyncio.run(dbgate.call("connections/list"))


def test_login_without_token_raises(http):
    http.script += [FakeResponse(payload={"error": "nope"})]

    with pytest.raises(dbgate.DbGateError, match="no accessToken"):
        asyncio.run(dbgate.call("connections/list"))


def test_sql_error_in_body_raises_despite_http_200(http):
    http.script += [
        _login_ok(),
        FakeResponse(payload={"msgtype": "response", "errorMessage": "no such table"}),
    ]

    with pytest.raises(dbgate.DbGateError, match="no such table"):
        asyncio.run(dbgate.call("database-connections/query-data", {"sql": "select 1"}))


def test_api_error_in_body_raises(http):
    http.script += [_login_ok(), FakeResponse(payload={"apiErrorMessage": "boom"})]

    with pytest.raises(dbgate.DbGateError, match="boom"):
        asyncio.run(dbgate.call("connections/list"))


def test_expired_token_triggers_relogin_and_retry(http):
    http.script += [
        _login_ok("old"),
        FakeResponse(status_code=401, payload=None),
        _login_ok("fresh"),
        FakeResponse(payload=[{"_id": "con1"}]),
    ]

    out = asyncio.run(dbgate.call("connections/list"))

    assert out == [{"_id": "con1"}]
    assert http.calls[-1]["headers"]["Authorization"] == "Bearer fresh"
    assert len([c for c in http.calls if c["url"].endswith("/auth/login")]) == 2


def test_second_401_raises(http):
    http.script += [
        _login_ok("old"),
        FakeResponse(status_code=401, payload=None, text="missing authorization header"),
        _login_ok("fresh"),
        FakeResponse(status_code=401, payload=None, text="missing authorization header"),
    ]

    with pytest.raises(dbgate.DbGateError, match="HTTP 401"):
        asyncio.run(dbgate.call("connections/list"))


def test_non_200_raises_with_status_and_body(http):
    http.script += [_login_ok(), FakeResponse(status_code=404, payload=None, text="Cannot POST")]

    with pytest.raises(dbgate.DbGateError, match="HTTP 404"):
        asyncio.run(dbgate.call("nope/whatever"))


def test_no_transport_when_no_tunnel_is_configured(http):
    http.script += [_login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))

    assert http.opts[0]["transport"] is None


def test_tunnel_routes_calls_through_a_tunneled_transport(http):
    dbgate.use_tunnel("127.0.0.1", 15999)
    http.script += [_login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))

    sent = http.opts[0]["transport"]
    assert isinstance(sent, TunneledTransport)
    assert (sent._host, sent._port) == ("127.0.0.1", 15999)


def test_tunnel_leaves_the_dashboard_url_canonical(http, monkeypatch):
    """The transport moves, the URL does not — that is what keeps Host and SNI right."""
    monkeypatch.setenv("DBGATE_URL", "https://dbgate.example.com")
    dbgate.use_tunnel("127.0.0.1", 15999)
    http.script += [_login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))

    assert dbgate.base_url() == "https://dbgate.example.com"
    assert http.calls[0]["url"] == "https://dbgate.example.com/auth/login"


def _dropped():
    """What httpx raises once the forwarded port stops accepting."""
    return httpx.ConnectError("")


def test_transport_error_without_a_tunnel_names_the_failure(http):
    """The bare exception message is often empty — the class and URL are not."""
    http.script += [_dropped()]

    with pytest.raises(dbgate.DbGateError, match="ConnectError"):
        asyncio.run(dbgate.call("connections/list"))


def test_dropped_tunnel_is_reopened_and_the_call_retried(http):
    dbgate.use_tunnel("127.0.0.1", 15999)
    dbgate.on_tunnel_lost(lambda: ("127.0.0.1", 16001))
    http.script += [_dropped(), _login_ok(), FakeResponse(payload=[{"_id": "con1"}])]

    out = asyncio.run(dbgate.call("connections/list"))

    assert out == [{"_id": "con1"}]
    assert dbgate._tunnel == ("127.0.0.1", 16001)


def test_reopening_tells_the_agent_what_happened(http):
    dbgate.use_tunnel("127.0.0.1", 15999)
    dbgate.on_tunnel_lost(lambda: ("127.0.0.1", 16001))
    http.script += [_dropped(), _login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))

    notices = dbgate.take_notices()
    assert len(notices) == 1
    assert "ConnectError" in notices[0]
    assert "16001" in notices[0]


def test_notices_drain_so_they_are_reported_once(http):
    dbgate.use_tunnel("127.0.0.1", 15999)
    dbgate.on_tunnel_lost(lambda: ("127.0.0.1", 16001))
    http.script += [_dropped(), _login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))

    assert dbgate.take_notices() != []
    assert dbgate.take_notices() == []


def test_a_second_drop_gives_up_instead_of_looping(http):
    dbgate.use_tunnel("127.0.0.1", 15999)
    dbgate.on_tunnel_lost(lambda: ("127.0.0.1", 16001))
    http.script += [_dropped(), _dropped()]

    with pytest.raises(dbgate.DbGateError, match="ConnectError"):
        asyncio.run(dbgate.call("connections/list"))

    assert http.script == []  # exactly two attempts, no third


def test_relogin_happens_after_a_reopen(http):
    """The old token is fine, but the fresh client must still authenticate."""
    dbgate.use_tunnel("127.0.0.1", 15999)
    dbgate.on_tunnel_lost(lambda: ("127.0.0.1", 16001))
    http.script += [_login_ok(), _dropped(), _login_ok(), FakeResponse(payload=[])]

    asyncio.run(dbgate.call("connections/list"))
    asyncio.run(dbgate.call("connections/list"))

    assert http.script == []
