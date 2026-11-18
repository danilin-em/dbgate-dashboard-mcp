#!/bin/bash
# Loads Chinook into MongoDB. The entrypoint runs it only when /data/db is empty.
#
# IMPORTANT: the mongo entrypoint does not execute this file, it sources it (`. "$f"`).
# Hence: (1) $0 points to the entrypoint itself, so the data path must be absolute;
# (2) the body is wrapped in a subshell so `set -e` and variables do not leak into the entrypoint;
# (3) no `exit` — it would kill the whole entrypoint.
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
