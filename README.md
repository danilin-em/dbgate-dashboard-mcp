# dbgate-dashboard-mcp

An MCP server that gives an agent access to databases **through a running [DbGate](https://dbgate.org/) dashboard**. No database drivers are needed: DbGate itself holds the connections, engine plugins and pool, and the server talks to it over HTTP.

Engines: MySQL, PostgreSQL, SQLite (SQL tools), MongoDB and Redis (dedicated tools below).

## Installation

```jsonc
// .mcp.json
{
  "mcpServers": {
    "dbgate-dashboard": {
      "command": "uvx",
      "args": ["dbgate-dashboard-mcp@latest"],
      "env": {
        "DBGATE_URL": "http://localhost:3000"
      }
    }
  }
}
```

A remote dashboard through an SSH tunnel — `DBGATE_URL` then means the address **on the remote side**:

```jsonc
"env": {
  "DBGATE_URL": "http://localhost:3000",
  "SSH_PROXY_TO": "user@remote-host"
}
```

An HTTPS dashboard behind a reverse proxy works the same way — the port comes from the scheme (443), while `Host` and SNI stay canonical, so certificate verification is not disabled:

```jsonc
"env": {
  "DBGATE_URL": "https://dbgate.example.com",
  "SSH_PROXY_TO": "user@remote-host"
}
```

The tunnel only changes the connect address: `DBGATE_URL` is not rewritten, otherwise the proxy would receive `Host: 127.0.0.1:<port>` and answer with a redirect, and TLS would not match the certificate.

### Tunnel loss

If ssh dies mid-session, the next call does not fail: the tunnel is reopened and the request is retried **once**. A second drop in a row is returned as an error, so an unreachable remote host does not send every tool into endless retries.

A reconnect is invisible by itself — a retried call looks like an ordinary one — so the tool result gets a `notices` field:

```jsonc
{
  "connections": [ /* ... */ ],
  "notices": [
    "ssh tunnel dropped (ConnectError) and was reopened on 127.0.0.1:49173; the call was retried and this result is from the new connection"
  ]
}
```

The field appears only when there is something to report, and exactly once: the queue is drained when delivered.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DBGATE_URL` | `http://localhost:3000` | dashboard address (with a tunnel — the remote side) |
| `DBGATE_LOGIN` | — | login, if authentication is enabled in DbGate |
| `DBGATE_PASSWORD` | — | its password |
| `SSH_PROXY_TO` | — | `user@host` for the SSH tunnel (`ssh -N -L`) |
| `SSH_PROXY_PORT` | auto | fixed local port for the tunnel |

## Tools

| Tool | What it does |
|---|---|
| `list_connections` | dashboard connections: `conid`, engine, default database |
| `list_databases` | databases on a connection |
| `list_tables` | tables, views and matviews (no columns) |
| `describe_table` | columns, PK, FKs and indexes of one object |
| `query` | a read query; appends `LIMIT` and truncates the result |
| `execute` | any SQL, including writes and DDL, unmodified |
| `find_documents` | documents of a Mongo collection with filter and sort |
| `redis_keys` | browse the Redis keyspace level by level |
| `redis_get` | type, TTL and value of one key |
| `redis_command` | any Redis command, including writes |

`conid` is required by every tool — take it from `list_connections`. `database` can be omitted: the connection's default database is used.

`query` limits the output twice: it appends `LIMIT n` if the SQL has no limit of its own, and in any case cuts the response to `n` rows (`truncated: true` when cut). The DbGate API has no limit parameter, so the bound has to be held on both sides.

`LIMIT` is appended only to statements that accept it (`SELECT`, `WITH`, `VALUES`, `TABLE`). `SHOW`, `PRAGMA`, `DESCRIBE`, `EXPLAIN` go to the engine as is — with a `LIMIT` glued on they would be a syntax error; cutting to `n` rows still applies to them.

`execute` passes SQL through unchanged and without a safety net — including `INSERT`/`UPDATE`/`DELETE` and DDL. Restrict permissions on the connection itself in DbGate.

## MongoDB and Redis

`find_documents` takes a regular Mongo filter — `{"artistId": {"$gt": 5}}`, `{"title": {"$regex": "Rock"}}`, `$in`, `$and`/`$or`. It is translated into a DbGate condition, because the dashboard API does not accept a raw Mongo filter. An unsupported operator (`$exists`, `$elemMatch`) returns an explicit error. `$regex` becomes a LIKE wrapped in `%…%`, so `%` and `_` inside the pattern act as wildcards.

Collections show up in the common `list_tables` with `type: "collection"`; `describe_table` for a collection returns the document count and the unique key — DbGate's Mongo structure has no columns.

Redis navigation has two levels: `redis_keys` without `root` shows prefixes (`album`, `track`, …), with `root` — the keys under it. `redis_get` reads a value as one page no longer than `limit` (`truncated: true` if the value continues); for the full data of a large key use `redis_command`, which runs an arbitrary command. For a missing key `redis_get` returns the error `key '…' not found`.

The command name in `redis_command` is case-insensitive — it is lowercased, because DbGate calls the method of the same name on its Redis client, and all those methods are lowercase.

A Redis connection has no default database, so `database` can be omitted: the server takes the first one in the list (`db0`). A freshly opened DbGate connection returns an empty database list while it starts up, so an empty answer is asked again — without that, database resolution sometimes failed with `has no default database`.

## Development

```bash
uv sync                 # venv + runtime + dev group
uv run pytest           # full suite; addopts enforces --cov-fail-under=100
uv run pytest tests/test_server.py --no-cov   # partial run — only with --no-cov
python -m dbgate_dashboard_mcp                # run over stdio
```

Tests need neither network nor a running DbGate: `httpx` and `subprocess` are faked.

## Manual check on the test stand

The repository ships a `docker-compose.yml` with DbGate and five databases (Chinook in MySQL, PostgreSQL, SQLite, MongoDB, Redis). Seeds live in `init/`: [Chinook](https://github.com/lerocha/chinook-database) (MIT, © Luis Rocha), adapted for each engine.

```bash
docker compose up -d
TOKEN=$(curl -s -X POST http://localhost:3000/auth/login \
  -H 'Content-Type: application/json' -d '{"amoid":"none"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')
curl -s -X POST http://localhost:3000/connections/list \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
```

Then connect the server in an MCP client with `DBGATE_URL=http://localhost:3000` and call `list_connections`.

## How the DbGate API works

Useful when extending the server: DbGate is RPC over HTTP, not REST. Controller methods are mounted as kebab-case paths (`queryData` → `/database-connections/query-data`), almost all of them `POST` with a single JSON object of arguments. `POST /auth/login {"amoid":"none"}` issues a token that lives 24 h. SQL errors arrive with **HTTP 200** and an `errorMessage` field, so success is decided by the response body, not the status code.
