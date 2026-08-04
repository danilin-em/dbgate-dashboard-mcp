"""MCP server exposing DbGate dashboard databases as MCP tools.

Runs over stdio (the default transport) as a console script or via
``python -m dbgate_dashboard_mcp``. The dashboard endpoint comes from
``DBGATE_URL`` (default ``http://localhost:3000``).

Every tool returns a dict and never raises: failures come back as
``{"error": "..."}``, which is the contract MCP clients rely on.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer

from . import dbgate
from .tunnel import SshLocalForwardTunnel


def _env_int(name: str) -> int | None:
    """Read an int env var; ``None`` when unset or blank."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _split_endpoint(url: str) -> tuple[str, int]:
    """Parse a dashboard URL into the ``(host, port)`` an ``ssh -L`` targets."""
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or 3000


# When SSH_PROXY_TO is set, DBGATE_URL is the *remote-side* endpoint that gets
# forwarded; main() rewrites DBGATE_URL to the local forwarded URL at startup.
SSH_PROXY_TO = os.environ.get("SSH_PROXY_TO") or None
SSH_PROXY_PORT = _env_int("SSH_PROXY_PORT")

DEFAULT_ROW_LIMIT = 200

mcp = MCPServer("dbgate-dashboard-mcp")

_connections: list[dict] | None = None


class _ResolveError(Exception):
    """Raised when a conid / database / table can't be resolved."""


def _reset_cache() -> None:
    """Forget the cached connection list (used by tests)."""
    global _connections
    _connections = None


async def _load_connections(refresh: bool = False) -> list[dict]:
    """Connection list from DbGate, cached for the lifetime of the process."""
    global _connections
    if _connections is None or refresh:
        _connections = await dbgate.call("connections/list")
    return _connections


async def _find_connection(conid: str) -> dict:
    """Look up one connection, refreshing the cache once before giving up."""
    for refresh in (False, True):
        for conn in await _load_connections(refresh=refresh):
            if conn.get("_id") == conid:
                return conn
    raise _ResolveError(f"unknown conid '{conid}' — call list_connections first")


async def _resolve_database(conid: str, database: str | None) -> str:
    """Return ``database`` or the connection's default database."""
    if database:
        return database
    conn = await _find_connection(conid)
    default = conn.get("defaultDatabase")
    if not default:
        raise _ResolveError(
            f"connection '{conid}' has no default database — pass database explicitly"
        )
    return default


@mcp.tool()
async def list_connections() -> dict:
    """List the database connections configured in the DbGate dashboard."""
    try:
        conns = await _load_connections(refresh=True)
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "connections": [
            {
                "conid": c.get("_id"),
                "label": c.get("displayName"),
                "engine": c.get("engine"),
                "default_database": c.get("defaultDatabase"),
                "single_database": bool(c.get("singleDatabase")),
            }
            for c in conns
        ]
    }


@mcp.tool()
async def list_databases(conid: str) -> dict:
    """List databases available on a connection.

    DbGate returns an empty list for connections pinned to one database
    (``singleDatabase``); in that case the connection's default database is
    reported with ``single_database: true``.
    """
    try:
        conn = await _find_connection(conid)
        rows = await dbgate.call("server-connections/list-databases", {"conid": conid})
    except Exception as exc:
        return {"error": str(exc)}
    names = [r.get("name") for r in rows if r.get("name")]
    if names:
        return {"databases": names, "single_database": False}
    default = conn.get("defaultDatabase")
    if default:
        return {"databases": [default], "single_database": True}
    return {"databases": [], "single_database": False}


def main() -> None:
    """Console-script entry point: run the MCP server over stdio.

    When ``SSH_PROXY_TO`` is set, open an ``ssh -L`` tunnel to the dashboard
    first and repoint ``DBGATE_URL`` at the forwarded local port; the client
    reads that env var on every call, so tools need no changes. The tunnel
    lives exactly as long as the server.
    """
    tunnel: SshLocalForwardTunnel | None = None
    if SSH_PROXY_TO:
        host, port = _split_endpoint(dbgate.base_url())
        tunnel = SshLocalForwardTunnel(SSH_PROXY_TO, host, port, SSH_PROXY_PORT)
        tunnel.start()
        os.environ["DBGATE_URL"] = tunnel.local_url
    try:
        mcp.run()
    finally:
        if tunnel is not None:
            tunnel.stop()


if __name__ == "__main__":
    main()
