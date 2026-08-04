"""Tool-level tests — the DbGate client is stubbed, no dashboard needed."""

import asyncio

import pytest

import dbgate_dashboard_mcp.server as server
from dbgate_dashboard_mcp.dbgate import DbGateError

CONNECTIONS = [
    {
        "_id": "con1",
        "displayName": "MySQL",
        "engine": "mysql@dbgate-plugin-mysql",
        "defaultDatabase": "Chinook",
        "singleDatabase": True,
    },
    {
        "_id": "con2",
        "displayName": "PostgreSQL",
        "engine": "postgres@dbgate-plugin-postgres",
        "defaultDatabase": "chinook",
        "singleDatabase": True,
    },
    {
        "_id": "con6",
        "displayName": "No default",
        "engine": "postgres@dbgate-plugin-postgres",
        "defaultDatabase": None,
        "singleDatabase": False,
    },
]


STRUCTURE = {
    "engine": "postgres@dbgate-plugin-postgres",
    "tables": [
        {
            "pureName": "artist",
            "schemaName": "public",
            "sizeBytes": 8192,
            "objectId": 16390,
            "contentHash": "abc",
            "columns": [
                {
                    "columnName": "artist_id",
                    "dataType": "integer",
                    "notNull": True,
                    "defaultValue": None,
                    "autoIncrement": False,
                    "postgresColumnId": 1,
                },
                {
                    "columnName": "name",
                    "dataType": "character varying(120)",
                    "notNull": False,
                    "defaultValue": None,
                    "autoIncrement": False,
                    "postgresColumnId": 2,
                },
            ],
            "primaryKey": {
                "constraintName": "artist_pkey",
                "columns": [{"columnName": "artist_id"}],
            },
            "foreignKeys": [],
            "indexes": [],
            "uniques": [],
        },
        {
            "pureName": "album",
            "schemaName": "public",
            "sizeBytes": 16384,
            "columns": [{"columnName": "album_id", "dataType": "integer"}],
            "primaryKey": None,
            "foreignKeys": [
                {
                    "constraintName": "album_artist_id_fkey",
                    "refSchemaName": "public",
                    "refTableName": "artist",
                    "updateAction": "NO ACTION",
                    "deleteAction": "NO ACTION",
                    "columns": [
                        {"columnName": "artist_id", "refColumnName": "artist_id"}
                    ],
                }
            ],
            "indexes": [
                {
                    "constraintName": "album_artist_id_idx",
                    "isUnique": False,
                    "columns": [{"columnName": "artist_id", "isDescending": False}],
                }
            ],
            "uniques": [],
        },
        {
            "pureName": "album",
            "schemaName": "reporting",
            "columns": [],
            "primaryKey": None,
            "foreignKeys": [],
            "indexes": [],
            "uniques": [],
        },
    ],
    "views": [
        {"pureName": "top_sellers", "schemaName": "public", "columns": [{"columnName": "x"}]}
    ],
    "matviews": [],
}


def _fn(tool):
    # Works whether @mcp.tool() returns the raw function or a Tool wrapper.
    return getattr(tool, "fn", tool)


def _stub(monkeypatch, responses):
    """Route dbgate.call by path.

    `responses` maps a path to a value, an Exception instance (raised), or a
    callable taking the payload. Records every (path, payload) in `calls`.
    """
    calls = []

    async def fake_call(path, payload=None):
        calls.append((path, payload or {}))
        if path not in responses:
            raise AssertionError(f"unexpected path {path}")
        value = responses[path]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(payload or {})
        return value

    monkeypatch.setattr(server.dbgate, "call", fake_call)
    server._reset_cache()
    return calls


def test_list_connections_shapes_rows(monkeypatch):
    _stub(monkeypatch, {"connections/list": CONNECTIONS})

    out = asyncio.run(_fn(server.list_connections)())

    assert out["connections"][0] == {
        "conid": "con1",
        "label": "MySQL",
        "engine": "mysql@dbgate-plugin-mysql",
        "default_database": "Chinook",
        "single_database": True,
    }
    assert len(out["connections"]) == 3


def test_list_connections_reports_error(monkeypatch):
    _stub(monkeypatch, {"connections/list": DbGateError("connection refused")})

    out = asyncio.run(_fn(server.list_connections)())

    assert out == {"error": "connection refused"}


def test_resolve_database_uses_default(monkeypatch):
    _stub(monkeypatch, {"connections/list": CONNECTIONS})

    assert asyncio.run(server._resolve_database("con2", None)) == "chinook"


def test_resolve_database_prefers_explicit_value(monkeypatch):
    calls = _stub(monkeypatch, {"connections/list": CONNECTIONS})

    assert asyncio.run(server._resolve_database("con2", "other")) == "other"
    assert calls == []  # explicit database needs no connection lookup


def test_resolve_database_without_default_raises(monkeypatch):
    _stub(monkeypatch, {"connections/list": CONNECTIONS})

    with pytest.raises(server._ResolveError, match="no default database"):
        asyncio.run(server._resolve_database("con6", None))


def test_find_connection_refetches_once_for_unknown_conid(monkeypatch):
    calls = _stub(monkeypatch, {"connections/list": CONNECTIONS})

    with pytest.raises(server._ResolveError, match="unknown conid 'nope'"):
        asyncio.run(server._find_connection("nope"))

    assert len(calls) == 2  # cached list, then one forced refresh


def test_connections_are_cached_between_lookups(monkeypatch):
    calls = _stub(monkeypatch, {"connections/list": CONNECTIONS})

    asyncio.run(server._find_connection("con1"))
    asyncio.run(server._find_connection("con2"))

    assert len(calls) == 1


def test_list_databases_returns_server_list(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "server-connections/list-databases": [{"name": "chinook"}, {"name": "postgres"}],
        },
    )

    out = asyncio.run(_fn(server.list_databases)(conid="con2"))

    assert out == {"databases": ["chinook", "postgres"], "single_database": False}


def test_list_databases_falls_back_to_default_when_empty(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "server-connections/list-databases": [],
        },
    )

    out = asyncio.run(_fn(server.list_databases)(conid="con2"))

    assert out == {"databases": ["chinook"], "single_database": True}


def test_list_databases_empty_without_default(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "server-connections/list-databases": [],
        },
    )

    out = asyncio.run(_fn(server.list_databases)(conid="con6"))

    assert out == {"databases": [], "single_database": False}


def test_list_databases_reports_unknown_conid(monkeypatch):
    _stub(monkeypatch, {"connections/list": CONNECTIONS})

    out = asyncio.run(_fn(server.list_databases)(conid="nope"))

    assert "unknown conid 'nope'" in out["error"]


def test_list_tables_flattens_tables_and_views(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.list_tables)(conid="con2"))

    assert out["database"] == "chinook"
    assert {"schema": "public", "name": "artist", "type": "table",
            "columns": 2, "size_bytes": 8192} in out["objects"]
    assert {"schema": "public", "name": "top_sellers", "type": "view",
            "columns": 1, "size_bytes": None} in out["objects"]
    assert len(out["objects"]) == 4


def test_list_tables_filters_by_schema(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.list_tables)(conid="con2", schema="reporting"))

    assert [o["name"] for o in out["objects"]] == ["album"]


def test_list_tables_passes_explicit_database(monkeypatch):
    calls = _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    asyncio.run(_fn(server.list_tables)(conid="con2", database="other"))

    assert calls[0] == (
        "database-connections/structure",
        {"conid": "con2", "database": "other"},
    )


def test_list_tables_reports_error(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/structure": DbGateError("database not found"),
        },
    )

    out = asyncio.run(_fn(server.list_tables)(conid="con2"))

    assert out == {"error": "database not found"}


def test_describe_table_returns_columns_and_pk(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.describe_table)(conid="con2", table="artist"))

    assert out["schema"] == "public"
    assert out["name"] == "artist"
    assert out["type"] == "table"
    assert out["columns"][0] == {
        "name": "artist_id",
        "type": "integer",
        "not_null": True,
        "default": None,
        "auto_increment": False,
    }
    assert out["primary_key"] == {
        "name": "artist_pkey",
        "columns": ["artist_id"],
    }
    assert out["foreign_keys"] == []


def test_describe_table_returns_foreign_keys_and_indexes(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(
        _fn(server.describe_table)(conid="con2", table="album", schema="public")
    )

    assert out["primary_key"] is None
    assert out["foreign_keys"] == [
        {
            "name": "album_artist_id_fkey",
            "columns": ["artist_id"],
            "references": "public.artist",
            "ref_columns": ["artist_id"],
            "on_update": "NO ACTION",
            "on_delete": "NO ACTION",
        }
    ]
    assert out["indexes"] == [
        {"name": "album_artist_id_idx", "unique": False, "columns": ["artist_id"]}
    ]
    assert out["uniques"] == []


def test_describe_table_accepts_qualified_name(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.describe_table)(conid="con2", table="reporting.album"))

    assert out["schema"] == "reporting"


def test_describe_table_ambiguous_name_errors(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.describe_table)(conid="con2", table="album"))

    assert "ambiguous" in out["error"]
    assert "public.album" in out["error"]
    assert "reporting.album" in out["error"]


def test_describe_table_unknown_name_errors(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.describe_table)(conid="con2", table="ghost"))

    assert "no table, view or matview named 'ghost'" in out["error"]


def test_describe_table_describes_a_view(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.describe_table)(conid="con2", table="top_sellers"))

    assert out["type"] == "view"
    assert out["primary_key"] is None


def test_split_endpoint_parses_host_and_port():
    assert server._split_endpoint("http://dbgate.internal:3000") == (
        "dbgate.internal",
        3000,
    )


def test_split_endpoint_defaults_port_to_3000():
    assert server._split_endpoint("http://127.0.0.1") == ("127.0.0.1", 3000)


def test_env_int_blank_is_none(monkeypatch):
    monkeypatch.delenv("SSH_PROXY_PORT", raising=False)
    assert server._env_int("SSH_PROXY_PORT") is None
    monkeypatch.setenv("SSH_PROXY_PORT", "  ")
    assert server._env_int("SSH_PROXY_PORT") is None
    monkeypatch.setenv("SSH_PROXY_PORT", "7000")
    assert server._env_int("SSH_PROXY_PORT") == 7000


def test_main_runs_without_tunnel(monkeypatch):
    ran = []
    monkeypatch.setattr(server, "SSH_PROXY_TO", None)
    monkeypatch.setattr(server.mcp, "run", lambda: ran.append("run"))

    server.main()

    assert ran == ["run"]


def test_main_opens_tunnel_and_repoints_url(monkeypatch):
    events = []

    class FakeTunnel:
        def __init__(self, target, host, port, local_port):
            events.append(("init", target, host, port, local_port))
            self.local_url = "http://127.0.0.1:15999"

        def start(self):
            events.append(("start",))

        def stop(self):
            events.append(("stop",))

    monkeypatch.setenv("DBGATE_URL", "http://dbgate.internal:3000")
    monkeypatch.setattr(server, "SSH_PROXY_TO", "user@remote")
    monkeypatch.setattr(server, "SSH_PROXY_PORT", None)
    monkeypatch.setattr(server, "SshLocalForwardTunnel", FakeTunnel)
    monkeypatch.setattr(
        server.mcp,
        "run",
        lambda: events.append(("url", server.os.environ["DBGATE_URL"])),
    )

    server.main()

    assert events == [
        ("init", "user@remote", "dbgate.internal", 3000, None),
        ("start",),
        ("url", "http://127.0.0.1:15999"),
        ("stop",),
    ]
