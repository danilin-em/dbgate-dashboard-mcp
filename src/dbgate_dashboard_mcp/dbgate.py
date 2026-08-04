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
from typing import Any

import httpx

DEFAULT_URL = "http://localhost:3000"
TIMEOUT = 30.0

_token: str | None = None


class DbGateError(Exception):
    """DbGate refused the request or reported an error in the response body."""


def base_url() -> str:
    """Dashboard base URL from ``DBGATE_URL``, without a trailing slash."""
    return os.environ.get("DBGATE_URL", DEFAULT_URL).rstrip("/")


def reset_token() -> None:
    """Drop the cached token (used by tests and after a 401)."""
    global _token
    _token = None


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


async def call(path: str, payload: dict[str, Any] | None = None) -> Any:
    """POST ``payload`` to a DbGate RPC ``path`` and return the parsed body.

    Raises :class:`DbGateError` on a failed login, a non-200 response, or a
    body carrying ``errorMessage`` / ``apiErrorMessage``.
    """
    global _token
    body = payload or {}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
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
