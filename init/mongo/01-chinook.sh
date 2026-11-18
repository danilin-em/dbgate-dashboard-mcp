#!/bin/bash
# Загружает Chinook в MongoDB. Выполняется entrypoint'ом только при пустом /data/db.
#
# ВАЖНО: mongo-entrypoint не запускает этот файл, а сорсит его (`. "$f"`).
# Поэтому: (1) $0 указывает на сам entrypoint, путь к данным только абсолютный;
# (2) тело обёрнуто в подоболочку, чтобы `set -e` и переменные не утекли в entrypoint;
# (3) никаких `exit` — они убили бы entrypoint целиком.
(
  set -euo pipefail

  DB="${MONGO_INITDB_DATABASE:-chinook}"
  DIR=/docker-entrypoint-initdb.d/data

  for file in "$DIR"/*.json; do
    collection="$(basename "$file" .json)"
    echo "chinook: importing $collection"
    mongoimport --db "$DB" --collection "$collection" --file "$file" --jsonArray --drop --quiet
  done

  echo "chinook: creating indexes"
  mongosh --quiet "$DB" --eval '
    db.albums.createIndex({ artistId: 1 });
    db.tracks.createIndex({ albumId: 1 });
    db.tracks.createIndex({ genreId: 1 });
    db.tracks.createIndex({ mediaTypeId: 1 });
    db.tracks.createIndex({ name: "text", composer: "text" });
    db.customers.createIndex({ supportRepId: 1 });
    db.customers.createIndex({ country: 1, city: 1 });
    db.employees.createIndex({ reportsTo: 1 });
    db.invoices.createIndex({ customerId: 1 });
    db.invoices.createIndex({ invoiceDate: 1 });
    db.invoiceLines.createIndex({ invoiceId: 1 });
    db.invoiceLines.createIndex({ trackId: 1 });
    db.playlistTracks.createIndex({ trackId: 1 });
  '

  echo "chinook: done, $(mongosh --quiet "$DB" --eval 'db.getCollectionNames().length') collections"
) || echo "chinook: MONGO SEED FAILED" >&2
