"""Thin HTTP client for the DbGate dashboard API. Zero MCP awareness.

DbGate exposes RPC-over-HTTP: controller methods are mounted as kebab-case
paths (``queryData`` → ``/database-connections/query-data``), every endpoint is
``POST`` and takes one JSON object of arguments. All calls require a JWT
obtained from ``POST /auth/login``; the token lives 24 h, so instead of
tracking its expiry we relogin once on a 401.

DbGate reports SQL failures with **HTTP 200** and an ``errorMessage`` field in
the body, so success is decided by the body, not by the status code.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from .transport import TunneledTransport

DEFAULT_URL = "http://localhost:3000"
TIMEOUT = 30.0

_token: str | None = None

# When an ssh tunnel is open, only the *connect address* changes: ``DBGATE_URL``
# keeps naming the dashboard, so every request still carries the real ``Host``
# and negotiates TLS against the real certificate.
_tunnel: tuple[str, int] | None = None
_rebuild: Callable[[], tuple[str, int]] | None = None
_notices: list[str] = []


class DbGateError(Exception):
    """DbGate refused the request or reported an error in the response body."""


def base_url() -> str:
    """Dashboard base URL from ``DBGATE_URL``, without a trailing slash."""
    return os.environ.get("DBGATE_URL", DEFAULT_URL).rstrip("/")


def reset_token() -> None:
    """Drop the cached token (used by tests and after a 401)."""
    global _token
    _token = None


def use_tunnel(host: str, port: int) -> None:
    """Route every subsequent call through a forwarded local endpoint."""
    global _tunnel
    _tunnel = (host, port)


def on_tunnel_lost(rebuild: Callable[[], tuple[str, int]] | None) -> None:
    """Register how to reopen the tunnel; it returns the new ``(host, port)``.

    Without one a dropped tunnel is simply reported. With one, the call that
    hit the dead port is retried once against the reopened forward.
    """
    global _rebuild
    _rebuild = rebuild


def take_notices() -> list[str]:
    """Drain the out-of-band messages worth showing the caller.

    A reconnect is invisible in the result — the retried call just succeeds —
    so the event is queued here for the tool layer to attach, and drained so it
    is reported once rather than on every later call.
    """
    global _notices
    notices, _notices = _notices, []
    return notices


def reset_tunnel() -> None:
    """Forget the tunnel, how to rebuild it, and anything pending about it."""
    global _tunnel, _rebuild, _notices
    _tunnel = None
    _rebuild = None
    _notices = []


def _client() -> httpx.AsyncClient:
    """An httpx client aimed at the tunnel when there is one."""
    transport = TunneledTransport(*_tunnel) if _tunnel else None
    return httpx.AsyncClient(timeout=TIMEOUT, transport=transport)


async def _login(client: httpx.AsyncClient) -> str:
    """Obtain a JWT. ``amoid=none`` is DbGate's no-auth-provider login."""
    payload: dict[str, Any] = {"amoid": "none"}
    login = os.environ.get("DBGATE_LOGIN")
    if login:
        payload["login"] = login
        payload["password"] = os.environ.get("DBGATE_PASSWORD") or ""
    resp = await client.post(f"{base_url()}/auth/login", json=payload)
    if resp.status_code != 200:
        raise DbGateError(f"login failed: HTTP {resp.status_code}")
    token = resp.json().get("accessToken")
    if not token:
        raise DbGateError("login failed: no accessToken in response")
    return token


async def _post(
    client: httpx.AsyncClient, path: str, payload: dict[str, Any], token: str
):
    return await client.post(
        f"{base_url()}/{path}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _attempt(path: str, body: dict[str, Any]) -> Any:
    """One round trip, on a client built against the current endpoint."""
    global _token
    async with _client() as client:
        if _token is None:
            _token = await _login(client)
        resp = await _post(client, path, body, _token)
        if resp.status_code == 401:
            _token = await _login(client)
            resp = await _post(client, path, body, _token)
        if resp.status_code != 200:
            raise DbGateError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict):
            error = data.get("errorMessage") or data.get("apiErrorMessage")
            if error:
                raise DbGateError(str(error))
        return data


def _unreachable(exc: httpx.TransportError) -> DbGateError:
    """Wrap a transport failure so it says something.

    httpx often raises these with an empty message, which turns the tools'
    ``{"error": str(exc)}`` contract into ``{"error": ""}`` — the class name and
    the endpoint are what actually identify the problem.
    """
    return DbGateError(f"{base_url()} unreachable: {type(exc).__name__}: {exc}".rstrip(": "))


async def call(path: str, payload: dict[str, Any] | None = None) -> Any:
    """POST ``payload`` to a DbGate RPC ``path`` and return the parsed body.

    Raises :class:`DbGateError` on a failed login, a non-200 response, or a
    body carrying ``errorMessage`` / ``apiErrorMessage``.

    A transport failure means the endpoint stopped accepting — with an ssh
    tunnel that usually means ssh died. When a rebuild callback is registered
    the tunnel is reopened and the call retried exactly once; a second failure
    is reported rather than retried, so a genuinely dead remote host fails fast
    instead of looping.
    """
    global _tunnel
    body = payload or {}
    try:
        return await _attempt(path, body)
    except httpx.TransportError as first:
        if _rebuild is None:
            raise _unreachable(first) from first
        _tunnel = _rebuild()
        _notices.append(
            f"ssh tunnel dropped ({type(first).__name__}) and was reopened on "
            f"{_tunnel[0]}:{_tunnel[1]}; the call was retried and this result is "
            f"from the new connection"
        )
        try:
            return await _attempt(path, body)
        except httpx.TransportError as second:
            raise _unreachable(second) from second
