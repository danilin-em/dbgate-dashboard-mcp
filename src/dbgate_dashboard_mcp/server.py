"""MCP server exposing DbGate dashboard databases as MCP tools.

Runs over stdio (the default transport) as a console script or via
``python -m dbgate_dashboard_mcp``. The dashboard endpoint comes from
``DBGATE_URL`` (default ``http://localhost:3000``).

Every tool returns a dict and never raises: failures come back as
``{"error": "..."}``, which is the contract MCP clients rely on.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer

from . import dbgate
from .mongo_filter import to_condition, to_sort
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
    """Return ``database``, the connection's default, or its first database.

    Redis connections carry no default database, so the list is consulted
    before giving up — that resolves to ``db0`` without an engine-specific
    branch.
    """
    if database:
        return database
    conn = await _find_connection(conid)
    default = conn.get("defaultDatabase")
    if default:
        return default
    rows = await dbgate.call("server-connections/list-databases", {"conid": conid})
    for row in rows:
        name = row.get("name")
        if name:
            return name
    raise _ResolveError(
        f"connection '{conid}' has no default database — pass database explicitly"
    )


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


_OBJECT_KINDS = (
    ("tables", "table"),
    ("views", "view"),
    ("matviews", "matview"),
    ("collections", "collection"),
)


async def _structure(conid: str, database: str) -> dict:
    """Analysed database structure as DbGate reports it."""
    return await dbgate.call(
        "database-connections/structure", {"conid": conid, "database": database}
    )


@mcp.tool()
async def list_tables(
    conid: str, database: str | None = None, schema: str | None = None
) -> dict:
    """List tables, views and materialized views of a database.

    Columns are deliberately omitted (only their count is reported) — use
    describe_table for the details of one object.
    """
    try:
        db = await _resolve_database(conid, database)
        structure = await _structure(conid, db)
    except Exception as exc:
        return {"error": str(exc)}
    objects = []
    for key, kind in _OBJECT_KINDS:
        for obj in structure.get(key) or []:
            if schema and obj.get("schemaName") != schema:
                continue
            objects.append(
                {
                    "schema": obj.get("schemaName"),
                    "name": obj.get("pureName"),
                    "type": kind,
                    "columns": len(obj.get("columns") or []),
                    "size_bytes": obj.get("sizeBytes"),
                }
            )
    return {"database": db, "objects": objects}


def _column_names(columns: list[dict] | None, key: str = "columnName") -> list[str]:
    return [c.get(key) for c in columns or []]


def _clean_constraint(constraint: dict | None) -> dict | None:
    if not constraint:
        return None
    return {
        "name": constraint.get("constraintName"),
        "columns": _column_names(constraint.get("columns")),
    }


def _clean_foreign_key(fk: dict) -> dict:
    return {
        "name": fk.get("constraintName"),
        "columns": _column_names(fk.get("columns")),
        "references": f"{fk.get('refSchemaName')}.{fk.get('refTableName')}"
        if fk.get("refSchemaName")
        else fk.get("refTableName"),
        "ref_columns": _column_names(fk.get("columns"), "refColumnName"),
        "on_update": fk.get("updateAction"),
        "on_delete": fk.get("deleteAction"),
    }


def _clean_index(index: dict) -> dict:
    return {
        "name": index.get("constraintName"),
        "unique": bool(index.get("isUnique")),
        "columns": _column_names(index.get("columns")),
    }


def _find_object(structure: dict, table: str, schema: str | None) -> tuple[dict, str]:
    """Locate one table/view/matview by (optionally qualified) name."""
    if schema is None and "." in table:
        schema, table = table.split(".", 1)
    matches = [
        (obj, kind)
        for key, kind in _OBJECT_KINDS
        for obj in structure.get(key) or []
        if obj.get("pureName") == table
        and (schema is None or obj.get("schemaName") == schema)
    ]
    if not matches:
        raise _ResolveError(f"no table, view or matview named '{table}'")
    if len(matches) > 1:
        names = ", ".join(f"{o.get('schemaName')}.{o.get('pureName')}" for o, _ in matches)
        raise _ResolveError(f"ambiguous table name '{table}' — pass schema: {names}")
    return matches[0]


@mcp.tool()
async def describe_table(
    conid: str,
    table: str,
    database: str | None = None,
    schema: str | None = None,
) -> dict:
    """Describe one table, view or matview: columns, keys, indexes.

    ``table`` may be qualified (``public.artist``) instead of passing ``schema``.
    """
    try:
        db = await _resolve_database(conid, database)
        structure = await _structure(conid, db)
        obj, kind = _find_object(structure, table, schema)
    except Exception as exc:
        return {"error": str(exc)}
    result = {
        "database": db,
        "schema": obj.get("schemaName"),
        "name": obj.get("pureName"),
        "type": kind,
        "columns": [
            {
                "name": c.get("columnName"),
                "type": c.get("dataType"),
                "not_null": c.get("notNull"),
                "default": c.get("defaultValue"),
                "auto_increment": c.get("autoIncrement"),
            }
            for c in obj.get("columns") or []
        ],
        "primary_key": _clean_constraint(obj.get("primaryKey")),
        "foreign_keys": [_clean_foreign_key(fk) for fk in obj.get("foreignKeys") or []],
        "indexes": [_clean_index(ix) for ix in obj.get("indexes") or []],
        "uniques": [_clean_constraint(u) for u in obj.get("uniques") or []],
    }
    # Mongo collections carry no column list; report what they do have.
    if obj.get("tableRowCount") is not None:
        result["rows"] = obj.get("tableRowCount")
    if obj.get("uniqueKey"):
        result["unique_key"] = _column_names(obj.get("uniqueKey"))
    return result


_LIMIT_RE = re.compile(r"\b(limit|fetch\s+first|fetch\s+next|top)\b", re.IGNORECASE)


def _apply_limit(sql: str, limit: int) -> str:
    """Append ``LIMIT n`` unless the query already caps its result set."""
    stripped = sql.strip().rstrip(";").rstrip()
    if _LIMIT_RE.search(stripped):
        return stripped
    return f"{stripped}\nLIMIT {limit}"


@mcp.tool()
async def query(
    conid: str,
    sql: str,
    database: str | None = None,
    limit: int = DEFAULT_ROW_LIMIT,
) -> dict:
    """Run a read query and return at most ``limit`` rows.

    ``LIMIT n`` is appended when the SQL has no cap of its own, and the result
    is truncated to ``limit`` regardless — DbGate's query API takes no row
    limit parameter, so the cap has to be enforced on both ends.

    Use execute for statements that change data or schema.
    """
    try:
        db = await _resolve_database(conid, database)
        result = await dbgate.call(
            "database-connections/query-data",
            {"conid": conid, "database": db, "sql": _apply_limit(sql, limit)},
        )
    except Exception as exc:
        return {"error": str(exc)}
    rows = result.get("rows") or []
    return {
        "database": db,
        "columns": [c.get("columnName") for c in result.get("columns") or []],
        "rows": rows[:limit],
        "row_count": len(rows[:limit]),
        "truncated": len(rows) > limit,
    }


@mcp.tool()
async def execute(conid: str, sql: str, database: str | None = None) -> dict:
    """Run any SQL statement, including writes and DDL.

    The SQL is passed to DbGate verbatim — no row limit is added. Use query for
    reads; this tool exists so that data and schema changes are an explicit,
    separately auditable action.
    """
    try:
        db = await _resolve_database(conid, database)
        result = await dbgate.call(
            "database-connections/query-data",
            {"conid": conid, "database": db, "sql": sql},
        )
    except Exception as exc:
        return {"error": str(exc)}
    rows = result.get("rows") or []
    return {
        "database": db,
        "columns": [c.get("columnName") for c in result.get("columns") or []],
        "rows": rows,
        "row_count": len(rows),
    }


@mcp.tool()
async def find_documents(
    conid: str,
    collection: str,
    filter: dict | None = None,
    sort: dict | None = None,
    skip: int = 0,
    limit: int = DEFAULT_ROW_LIMIT,
    database: str | None = None,
) -> dict:
    """Read documents from a MongoDB collection.

    ``filter`` is a normal Mongo filter document; the supported subset is
    equality, $eq/$ne/$gt/$gte/$lt/$lte, $in, $regex and $and/$or. $regex is
    translated to a LIKE with the pattern wrapped in ``%``, so ``%`` and ``_``
    inside it act as wildcards rather than literals.

    ``sort`` takes the Mongo form ``{"field": 1}`` / ``{"field": -1}``.
    """
    try:
        db = await _resolve_database(conid, database)
        options: dict = {"pureName": collection, "limit": limit, "skip": skip}
        condition = to_condition(filter)
        if condition is not None:
            options["condition"] = condition
        sort_spec = to_sort(sort)
        if sort_spec is not None:
            options["sort"] = sort_spec
        result = await dbgate.call(
            "database-connections/collection-data",
            {"conid": conid, "database": db, "options": options},
        )
    except Exception as exc:
        return {"error": str(exc)}
    rows = result.get("rows") or []
    return {
        "database": db,
        "collection": collection,
        "documents": rows[:limit],
        "count": len(rows[:limit]),
        "truncated": len(rows) > limit,
    }


@mcp.tool()
async def redis_keys(
    conid: str,
    root: str | None = None,
    filter: str | None = None,
    limit: int = DEFAULT_ROW_LIMIT,
    database: str | None = None,
) -> dict:
    """Browse the Redis key space one level at a time.

    Without ``root`` the reply lists key prefixes (``level: "prefixes"``);
    with it, the actual keys under that prefix (``level: "keys"``). Pass a
    prefix's ``name`` back as ``root`` to descend.
    """
    try:
        db = await _resolve_database(conid, database)
        nodes = await dbgate.call(
            "database-connections/load-keys",
            {
                "conid": conid,
                "database": db,
                "root": root or "",
                "filter": filter or "",
                "limit": limit,
            },
        )
    except Exception as exc:
        return {"error": str(exc)}
    shown = nodes[:limit]
    items = [
        {
            "name": n.get("text"),
            "key": n.get("key"),
            "type": n.get("type"),
            "count": n.get("count"),
        }
        if n.get("key")
        else {"name": n.get("root"), "count": n.get("count")}
        for n in shown
    ]
    return {
        "database": db,
        "level": "keys" if root else "prefixes",
        "items": items,
        "truncated": len(nodes) > limit,
    }


@mcp.tool()
async def redis_get(
    conid: str,
    key: str,
    limit: int = DEFAULT_ROW_LIMIT,
    database: str | None = None,
) -> dict:
    """Read one Redis key: its type, TTL and value.

    Strings come back in ``value``; hashes, lists, sets and sorted sets in
    ``items``, read in a single page of ``limit`` entries. ``truncated: true``
    means DbGate left its cursor open — use redis_command for the full value.
    """
    try:
        db = await _resolve_database(conid, database)
        info = await dbgate.call(
            "database-connections/load-key-info",
            {"conid": conid, "database": db, "key": key},
        )
        out = {
            "database": db,
            "key": info.get("key", key),
            "type": info.get("type"),
            "ttl": info.get("ttl"),
        }
        if "value" in info:
            out["value"] = info.get("value")
            return out
        page = await dbgate.call(
            "database-connections/load-key-table-range",
            {"conid": conid, "database": db, "key": key, "cursor": 0, "count": limit},
        )
    except Exception as exc:
        return {"error": str(exc)}
    out["items"] = page.get("items") or []
    out["truncated"] = bool(page.get("cursor"))
    return out


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
