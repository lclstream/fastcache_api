# fastcache_api

API for fastcache

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
