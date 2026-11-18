# dbgate-dashboard-mcp

MCP-сервер, который даёт агенту доступ к базам данных **через запущенный дашборд [DbGate](https://dbgate.org/)**. Драйверы БД не нужны: соединения, плагины движков и пул держит сам DbGate, а сервер общается с ним по HTTP.

Движки: MySQL, PostgreSQL, SQLite (SQL-инструменты), MongoDB и Redis (отдельные инструменты ниже).

## Установка

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

Удалённый дашборд через SSH-туннель — `DBGATE_URL` при этом означает адрес **на удалённой стороне**:

```jsonc
"env": {
  "DBGATE_URL": "http://localhost:3000",
  "SSH_PROXY_TO": "user@remote-host"
}
```

HTTPS-дашборд за реверс-прокси работает так же — порт берётся из схемы (443), а `Host` и SNI остаются каноническими, так что проверка сертификата не отключается:

```jsonc
"env": {
  "DBGATE_URL": "https://dbgate.example.com",
  "SSH_PROXY_TO": "user@remote-host"
}
```

Туннель меняет только адрес подключения: `DBGATE_URL` не переписывается, иначе прокси получил бы `Host: 127.0.0.1:<порт>` и ушёл в редирект, а TLS не сошёлся бы с сертификатом.

### Обрыв туннеля

Если ssh умирает посреди сессии, следующий вызов не падает: туннель поднимается заново и запрос повторяется **один раз**. Второй обрыв подряд уже возвращается ошибкой, чтобы недоступный удалённый хост не уводил каждый инструмент в бесконечные попытки.

Переподключение само по себе невидимо — повторённый вызов выглядит как обычный, — поэтому результат инструмента дополняется полем `notices`:

```jsonc
{
  "connections": [ /* ... */ ],
  "notices": [
    "ssh tunnel dropped (ConnectError) and was reopened on 127.0.0.1:49173; the call was retried and this result is from the new connection"
  ]
}
```

Поле появляется только когда есть что сообщить, и ровно один раз: очередь опустошается при выдаче.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DBGATE_URL` | `http://localhost:3000` | адрес дашборда (при туннеле — удалённая сторона) |
| `DBGATE_LOGIN` | — | логин, если в DbGate включена авторизация |
| `DBGATE_PASSWORD` | — | пароль к нему |
| `SSH_PROXY_TO` | — | `user@host` для SSH-туннеля (`ssh -N -L`) |
| `SSH_PROXY_PORT` | авто | фиксированный локальный порт туннеля |

## Инструменты

| Инструмент | Что делает |
|---|---|
| `list_connections` | соединения дашборда: `conid`, движок, БД по умолчанию |
| `list_databases` | базы на соединении |
| `list_tables` | таблицы, представления и matviews (без колонок) |
| `describe_table` | колонки, PK, FK, индексы одного объекта |
| `query` | читающий запрос; дописывает `LIMIT` и режет результат |
| `execute` | любой SQL, включая запись и DDL, без модификации |
| `find_documents` | документы Mongo-коллекции с фильтром и сортировкой |
| `redis_keys` | обзор пространства ключей Redis по уровням |
| `redis_get` | тип, TTL и значение одного ключа |
| `redis_command` | любая команда Redis, включая запись |

`conid` обязателен во всех инструментах — берётся из `list_connections`. `database` можно не передавать: подставляется БД по умолчанию у соединения.

`query` ограничивает выдачу дважды: дописывает `LIMIT n`, если в SQL нет собственного ограничения, и в любом случае режет ответ до `n` строк (`truncated: true` при обрезке). У API DbGate нет параметра лимита, поэтому предел приходится держать с обеих сторон.

`LIMIT` дописывается только к тем командам, что его принимают (`SELECT`, `WITH`, `VALUES`, `TABLE`). `SHOW`, `PRAGMA`, `DESCRIBE`, `EXPLAIN` уходят в движок как есть — с приклеенным `LIMIT` они были бы синтаксической ошибкой; обрезка до `n` строк для них всё равно работает.

`execute` пропускает SQL без изменений и без предохранителя — включая `INSERT`/`UPDATE`/`DELETE` и DDL. Ограничивайте права на стороне самого соединения в DbGate.

## MongoDB и Redis

`find_documents` принимает обычный Mongo-фильтр — `{"artistId": {"$gt": 5}}`, `{"title": {"$regex": "Rock"}}`, `$in`, `$and`/`$or`. Он транслируется в условие DbGate, потому что API дашборда сырой Mongo-фильтр не принимает. Неподдержанный оператор (`$exists`, `$elemMatch`) возвращает явную ошибку. `$regex` превращается в LIKE с обрамлением `%…%`, поэтому `%` и `_` внутри шаблона работают как подстановочные знаки.

Коллекции видны через общий `list_tables` с `type: "collection"`; `describe_table` для коллекции отдаёт число документов и уникальный ключ — колонок в структуре Mongo у DbGate нет.

Для Redis навигация двухуровневая: `redis_keys` без `root` показывает префиксы (`album`, `track`, …), с `root` — ключи под ним. `redis_get` читает значение одной страницей не длиннее `limit` (`truncated: true`, если значение продолжается); за полными данными большого ключа — `redis_command`, который выполняет произвольную команду. Несуществующий ключ `redis_get` возвращает ошибкой `key '…' not found`.

Имя команды в `redis_command` регистр не важен — оно приводится к нижнему, потому что DbGate вызывает метод одноимённо на своём Redis-клиенте, а там все методы строчные.

У Redis-соединения нет базы по умолчанию, поэтому `database` можно не передавать: сервер берёт первую из списка (`db0`). Свежеоткрытое соединение DbGate отдаёт пустой список баз, пока поднимается, поэтому пустой ответ переспрашивается — без этого резолв базы иногда падал с `has no default database`.

## Разработка

```bash
uv sync                 # venv + рантайм + dev-группа
uv run pytest           # весь набор; в addopts зашито --cov-fail-under=100
uv run pytest tests/test_server.py --no-cov   # частичный прогон — только с --no-cov
python -m dbgate_dashboard_mcp                # запуск по stdio
```

Тесты не требуют ни сети, ни запущенного DbGate: `httpx` и `subprocess` подменяются.

## Ручная проверка на тестовом стенде

В репозитории лежит `docker-compose.yml` с DbGate и пятью базами (Chinook в MySQL, PostgreSQL, SQLite, MongoDB, Redis). Сиды лежат в `init/`: [Chinook](https://github.com/lerocha/chinook-database) (MIT, © Luis Rocha), переложенный под каждую СУБД.

```bash
docker compose up -d
TOKEN=$(curl -s -X POST http://localhost:3000/auth/login \
  -H 'Content-Type: application/json' -d '{"amoid":"none"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')
curl -s -X POST http://localhost:3000/connections/list \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
```

Дальше — подключить сервер в MCP-клиенте с `DBGATE_URL=http://localhost:3000` и вызвать `list_connections`.

## Как устроен API DbGate

Полезно знать при доработке: DbGate — это RPC поверх HTTP, а не REST. Методы контроллеров смонтированы как kebab-case пути (`queryData` → `/database-connections/query-data`), почти все — `POST` с одним JSON-объектом аргументов. Токен выдаёт `POST /auth/login {"amoid":"none"}`, живёт 24 ч. Ошибки SQL приходят с **HTTP 200** и полем `errorMessage`, поэтому успех определяется телом ответа, а не кодом.
