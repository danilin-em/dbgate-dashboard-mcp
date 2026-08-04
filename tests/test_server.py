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


MONGO_STRUCTURE = {
    "engine": "mongo@dbgate-plugin-mongo",
    "tables": [],
    "collections": [
        {
            "pureName": "artists",
            "tableRowCount": 275,
            "sizeBytes": 12568,
            "uniqueKey": [{"columnName": "_id"}],
        },
        {"pureName": "albums", "tableRowCount": 347, "sizeBytes": 21782},
    ],
    "views": [],
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


def test_resolve_database_falls_back_to_first_database(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "server-connections/list-databases": [{"name": "db0"}, {"name": "db1"}],
        },
    )

    assert asyncio.run(server._resolve_database("con6", None)) == "db0"


def test_resolve_database_skips_unnamed_databases(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "server-connections/list-databases": [{"sortOrder": 0}, {"name": "db1"}],
        },
    )

    assert asyncio.run(server._resolve_database("con6", None)) == "db1"


def test_resolve_database_without_default_or_list_raises(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "server-connections/list-databases": [],
        },
    )

    with pytest.raises(server._ResolveError, match="no default database"):
        asyncio.run(server._resolve_database("con6", None))


def test_list_tables_includes_mongo_collections(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/structure": MONGO_STRUCTURE,
        },
    )

    out = asyncio.run(_fn(server.list_tables)(conid="con2"))

    assert out["objects"] == [
        {"schema": None, "name": "artists", "type": "collection",
         "columns": 0, "size_bytes": 12568},
        {"schema": None, "name": "albums", "type": "collection",
         "columns": 0, "size_bytes": 21782},
    ]


def test_describe_table_describes_a_collection(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/structure": MONGO_STRUCTURE,
        },
    )

    out = asyncio.run(_fn(server.describe_table)(conid="con2", table="artists"))

    assert out["type"] == "collection"
    assert out["columns"] == []
    assert out["rows"] == 275
    assert out["unique_key"] == ["_id"]


def test_describe_table_omits_collection_fields_for_sql(monkeypatch):
    _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/structure": STRUCTURE},
    )

    out = asyncio.run(_fn(server.describe_table)(conid="con2", table="artist"))

    assert "rows" not in out
    assert "unique_key" not in out


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


def test_apply_limit_appends_when_absent():
    assert server._apply_limit("select * from artist", 10) == (
        "select * from artist\nLIMIT 11"
    )


def test_apply_limit_strips_trailing_semicolon():
    assert server._apply_limit("select 1;  ", 5) == "select 1\nLIMIT 6"


def test_apply_limit_keeps_existing_limit():
    assert server._apply_limit("select 1 LIMIT 3", 10) == "select 1 LIMIT 3"


def test_apply_limit_keeps_fetch_first():
    sql = "select 1 fetch first 3 rows only"
    assert server._apply_limit(sql, 10) == sql


def test_apply_limit_keeps_top():
    sql = "select top 3 * from artist"
    assert server._apply_limit(sql, 10) == sql


def test_query_appends_limit_and_shapes_result(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": {
                "msgtype": "response",
                "rows": [{"artist_id": 1, "name": "AC/DC"}],
                "columns": [{"columnName": "artist_id"}, {"columnName": "name"}],
            },
        },
    )

    out = asyncio.run(_fn(server.query)(conid="con2", sql="select * from artist"))

    # calls[0] is the connections/list lookup that resolves the default database
    assert calls[1] == (
        "database-connections/query-data",
        {
            "conid": "con2",
            "database": "chinook",
            "sql": "select * from artist\nLIMIT 201",
        },
    )
    assert out == {
        "database": "chinook",
        "columns": ["artist_id", "name"],
        "rows": [{"artist_id": 1, "name": "AC/DC"}],
        "row_count": 1,
        "truncated": False,
    }


def test_query_truncates_rows_beyond_limit(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": {
                "rows": [{"n": i} for i in range(5)],
                "columns": [{"columnName": "n"}],
            },
        },
    )

    out = asyncio.run(
        _fn(server.query)(conid="con2", sql="select n from t limit 5", limit=2)
    )

    assert out["rows"] == [{"n": 0}, {"n": 1}]
    assert out["row_count"] == 2
    assert out["truncated"] is True


def test_query_detects_more_rows_from_the_extra_row(monkeypatch):
    # The tool asks for limit+1 rows; getting them back is what proves there
    # is more data, since the engine caps the result at exactly the limit.
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": {
                "rows": [{"n": i} for i in range(3)],
                "columns": [{"columnName": "n"}],
            },
        },
    )

    out = asyncio.run(_fn(server.query)(conid="con2", sql="select n from t", limit=2))

    assert out["rows"] == [{"n": 0}, {"n": 1}]
    assert out["truncated"] is True


def test_query_handles_missing_rows_key(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": {"msgtype": "response"},
        },
    )

    out = asyncio.run(_fn(server.query)(conid="con2", sql="select 1"))

    assert out["rows"] == []
    assert out["columns"] == []
    assert out["truncated"] is False


def test_query_reports_sql_error(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": DbGateError('relation "Artist" does not exist'),
        },
    )

    out = asyncio.run(_fn(server.query)(conid="con2", sql='select * from "Artist"'))

    assert out == {"error": 'relation "Artist" does not exist'}


def test_execute_passes_sql_through_untouched(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": {"msgtype": "response", "rows": [], "columns": []},
        },
    )

    out = asyncio.run(
        _fn(server.execute)(conid="con2", sql="insert into artist(name) values ('X');")
    )

    # calls[0] is the connections/list lookup that resolves the default database
    assert calls[1] == (
        "database-connections/query-data",
        {
            "conid": "con2",
            "database": "chinook",
            "sql": "insert into artist(name) values ('X');",
        },
    )
    assert out == {"database": "chinook", "columns": [], "rows": [], "row_count": 0}


def test_execute_returns_rows_when_engine_produces_them(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": {
                "rows": [{"id": 7}],
                "columns": [{"columnName": "id"}],
            },
        },
    )

    out = asyncio.run(
        _fn(server.execute)(conid="con2", sql="insert into t values (7) returning id")
    )

    assert out["rows"] == [{"id": 7}]
    assert out["columns"] == ["id"]
    assert out["row_count"] == 1


def test_execute_reports_error(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/query-data": DbGateError("syntax error at or near"),
        },
    )

    out = asyncio.run(_fn(server.execute)(conid="con2", sql="delte from t"))

    assert out == {"error": "syntax error at or near"}


def test_find_documents_translates_filter_and_sort(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/collection-data": {"rows": [{"_id": 1, "name": "AC/DC"}]},
        },
    )

    out = asyncio.run(
        _fn(server.find_documents)(
            conid="con2",
            collection="artists",
            filter={"name": "AC/DC"},
            sort={"name": 1},
            skip=5,
            limit=10,
        )
    )

    # calls[0] is the connections/list lookup that resolves the default database
    assert calls[1] == (
        "database-connections/collection-data",
        {
            "conid": "con2",
            "database": "chinook",
            "options": {
                "pureName": "artists",
                "limit": 11,
                "skip": 5,
                "condition": {
                    "conditionType": "binary",
                    "operator": "=",
                    "left": {"exprType": "column", "columnName": "name"},
                    "right": {"exprType": "value", "value": "AC/DC"},
                },
                "sort": [{"columnName": "name", "direction": "ASC"}],
            },
        },
    )
    assert out == {
        "database": "chinook",
        "collection": "artists",
        "documents": [{"_id": 1, "name": "AC/DC"}],
        "count": 1,
        "truncated": False,
    }


def test_find_documents_omits_empty_filter_and_sort(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/collection-data": {"rows": []},
        },
    )

    asyncio.run(_fn(server.find_documents)(conid="con2", collection="albums"))

    options = calls[1][1]["options"]
    assert options == {"pureName": "albums", "limit": 201, "skip": 0}


def test_find_documents_truncates(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/collection-data": {"rows": [{"n": i} for i in range(4)]},
        },
    )

    out = asyncio.run(
        _fn(server.find_documents)(conid="con2", collection="albums", limit=2)
    )

    assert out["documents"] == [{"n": 0}, {"n": 1}]
    assert out["count"] == 2
    assert out["truncated"] is True


def test_find_documents_asks_for_one_extra_document(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/collection-data": {"rows": [{"n": 0}, {"n": 1}]},
        },
    )

    out = asyncio.run(
        _fn(server.find_documents)(conid="con2", collection="albums", limit=1)
    )

    assert calls[1][1]["options"]["limit"] == 2
    assert out["documents"] == [{"n": 0}]
    assert out["truncated"] is True


def test_find_documents_reports_bad_filter(monkeypatch):
    _stub(monkeypatch, {"connections/list": CONNECTIONS})

    out = asyncio.run(
        _fn(server.find_documents)(
            conid="con2", collection="albums", filter={"a": {"$exists": True}}
        )
    )

    assert out == {"error": "unsupported filter operator '$exists'"}


def test_find_documents_reports_dbgate_error(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/collection-data": DbGateError("collection not found"),
        },
    )

    out = asyncio.run(_fn(server.find_documents)(conid="con2", collection="ghost"))

    assert out == {"error": "collection not found"}


def test_redis_keys_lists_prefixes(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-keys": [
                {"text": "album:*", "type": "none", "root": "album", "count": 694}
            ],
        },
    )

    out = asyncio.run(_fn(server.redis_keys)(conid="con2"))

    # calls[0] is the connections/list lookup that resolves the default database
    assert calls[1] == (
        "database-connections/load-keys",
        {"conid": "con2", "database": "chinook", "root": "", "filter": "", "limit": 201},
    )
    assert out["level"] == "prefixes"
    assert out["items"] == [{"name": "album", "count": 694}]


def test_redis_keys_lists_keys_under_root(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-keys": [
                {"text": "1", "key": "artist:1", "type": "hash", "count": 1}
            ],
        },
    )

    out = asyncio.run(_fn(server.redis_keys)(conid="con2", root="artist"))

    assert out["level"] == "keys"
    assert out["items"] == [
        {"name": "1", "key": "artist:1", "type": "hash", "count": 1}
    ]


def test_redis_keys_truncates(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-keys": [
                {"text": str(i), "key": f"k:{i}", "type": "string", "count": 1}
                for i in range(4)
            ],
        },
    )

    out = asyncio.run(_fn(server.redis_keys)(conid="con2", root="k", limit=2))

    assert len(out["items"]) == 2
    assert out["truncated"] is True


def test_redis_keys_asks_for_one_extra_node(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-keys": [
                {"text": "a", "root": "a", "count": 1},
                {"text": "b", "root": "b", "count": 1},
            ],
        },
    )

    out = asyncio.run(_fn(server.redis_keys)(conid="con2", limit=1))

    assert calls[1][1]["limit"] == 2
    assert out["items"] == [{"name": "a", "count": 1}]
    assert out["truncated"] is True


def test_redis_keys_reports_error(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-keys": DbGateError("not a redis connection"),
        },
    )

    out = asyncio.run(_fn(server.redis_keys)(conid="con2"))

    assert out == {"error": "not a redis connection"}


def test_redis_get_returns_string_value(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-key-info": {
                "key": "chinook:seeded", "type": "string", "ttl": -1, "value": "1"
            },
        },
    )

    out = asyncio.run(_fn(server.redis_get)(conid="con2", key="chinook:seeded"))

    # calls[0] is the connections/list lookup that resolves the default database
    assert calls[1] == (
        "database-connections/load-key-info",
        {"conid": "con2", "database": "chinook", "key": "chinook:seeded"},
    )
    assert out == {
        "database": "chinook",
        "key": "chinook:seeded",
        "type": "string",
        "ttl": -1,
        "value": "1",
    }


def test_redis_get_reads_hash_items(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-key-info": {
                "key": "artist:1", "type": "hash", "ttl": -1, "count": 1
            },
            "database-connections/load-key-table-range": {
                "cursor": 0,
                "items": [{"key": "name", "value": "AC/DC", "ttl": -1}],
            },
        },
    )

    out = asyncio.run(_fn(server.redis_get)(conid="con2", key="artist:1", limit=50))

    assert calls[2] == (
        "database-connections/load-key-table-range",
        {"conid": "con2", "database": "chinook", "key": "artist:1",
         "cursor": 0, "count": 50},
    )
    assert out["items"] == [{"key": "name", "value": "AC/DC", "ttl": -1}]
    assert out["truncated"] is False
    assert "value" not in out


def test_redis_get_marks_truncated_on_open_cursor(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-key-info": {
                "key": "big", "type": "list", "ttl": -1
            },
            "database-connections/load-key-table-range": {
                "cursor": 512, "items": [{"key": "0", "value": "x"}]
            },
        },
    )

    out = asyncio.run(_fn(server.redis_get)(conid="con2", key="big"))

    assert out["truncated"] is True


def test_redis_get_reports_error(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/load-key-info": DbGateError("key not found"),
        },
    )

    out = asyncio.run(_fn(server.redis_get)(conid="con2", key="ghost"))

    assert out == {"error": "key not found"}


def test_redis_command_passes_command_and_args(monkeypatch):
    calls = _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/call-method": {"name": "AC/DC"},
        },
    )

    out = asyncio.run(
        _fn(server.redis_command)(conid="con2", command="hgetall", args=["artist:1"])
    )

    # calls[0] is the connections/list lookup that resolves the default database
    assert calls[1] == (
        "database-connections/call-method",
        {"conid": "con2", "database": "chinook", "method": "hgetall",
         "args": ["artist:1"]},
    )
    assert out == {"database": "chinook", "result": {"name": "AC/DC"}}


def test_redis_command_defaults_args_to_empty(monkeypatch):
    calls = _stub(
        monkeypatch,
        {"connections/list": CONNECTIONS, "database-connections/call-method": 5274},
    )

    out = asyncio.run(_fn(server.redis_command)(conid="con2", command="dbsize"))

    assert calls[1][1]["args"] == []
    assert out["result"] == 5274


def test_redis_command_reports_error(monkeypatch):
    _stub(
        monkeypatch,
        {
            "connections/list": CONNECTIONS,
            "database-connections/call-method": DbGateError("unknown command"),
        },
    )

    out = asyncio.run(_fn(server.redis_command)(conid="con2", command="nope"))

    assert out == {"error": "unknown command"}


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
