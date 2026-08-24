"""Tests for the tunnel-aware HTTP transport — no network, no ssh."""

import asyncio

import httpx
import pytest

import dbgate_dashboard_mcp.transport as transport


def _request(url="https://dbgate.example.com/auth/login"):
    return httpx.Request("POST", url, json={"amoid": "none"})


def test_retarget_points_url_at_the_tunnel():
    req = _request()

    transport.retarget(req, "127.0.0.1", 15999)

    assert req.url.host == "127.0.0.1"
    assert req.url.port == 15999


def test_retarget_keeps_the_scheme_so_tls_still_happens():
    req = _request()

    transport.retarget(req, "127.0.0.1", 15999)

    assert req.url.scheme == "https"


def test_retarget_pins_sni_to_the_original_host():
    req = _request()

    transport.retarget(req, "127.0.0.1", 15999)

    assert req.extensions["sni_hostname"] == "dbgate.example.com"


def test_retarget_leaves_the_host_header_canonical():
    """The proxy 308-loops when Host carries a port, so it must stay bare."""
    req = _request()

    transport.retarget(req, "127.0.0.1", 15999)

    assert req.headers["Host"] == "dbgate.example.com"


def test_retarget_keeps_the_path_and_body():
    req = _request()
    body = req.content

    transport.retarget(req, "127.0.0.1", 15999)

    assert req.url.path == "/auth/login"
    assert req.content == body


def test_transport_retargets_then_delegates(monkeypatch):
    seen = []

    async def fake_parent(self, request):
        seen.append((request.url.host, request.url.port, request.headers["Host"]))
        return httpx.Response(200)

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", fake_parent
    )
    t = transport.TunneledTransport("127.0.0.1", 15999)

    resp = asyncio.run(t.handle_async_request(_request()))

    assert resp.status_code == 200
    assert seen == [("127.0.0.1", 15999, "dbgate.example.com")]


def test_transport_forwards_client_options_to_httpx():
    """`verify` and friends must still reach the real transport."""
    t = transport.TunneledTransport("127.0.0.1", 15999, retries=2)

    assert t._pool._retries == 2


@pytest.mark.parametrize("port", [443, 8443])
def test_retarget_survives_any_source_port(port):
    req = _request(f"https://dbgate.example.com:{port}/auth/login")

    transport.retarget(req, "127.0.0.1", 15999)

    assert req.url.port == 15999
    assert req.extensions["sni_hostname"] == "dbgate.example.com"
