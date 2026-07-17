# fastcache_api

API for fastcache

## Authentication

The API uses mutual TLS as its sole authentication boundary. The server accepts
only client certificates signed by `SSL_CA_CERTS` when
`REQUIRE_CLIENT_CERT=true`; staging and production refuse to start without
that configuration. Use a CA dedicated to the `lclstream_api` client.

`requested_by` is retained on cache records for audit purposes. It is metadata
from the trusted mTLS client, not an independently authenticated identity and
does not grant per-cache ownership.

## Database

Schema is managed by Alembic (`src/fastcache_api/alembic/`), not
create-on-boot. After changing `tables.py`, or on a fresh checkout:

```sh
uv run alembic upgrade head                      # apply migrations
uv run python scripts/gen_migration.py "message" # autogenerate a new one
```

`alembic upgrade head` must be run once before the first start (and again
after pulling any change that adds a migration); the systemd unit does this
automatically via `ExecStartPre`.
