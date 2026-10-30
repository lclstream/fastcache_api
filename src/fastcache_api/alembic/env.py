import logging

from alembic import context
from sqlalchemy import create_engine, pool

import fastcache_api.tables as _tables  # noqa: F401 - registers tables
from fastcache_api.config import settings
from fastcache_api.tables import Base

config = context.config
logging.basicConfig(level=logging.INFO)
target_metadata = Base.metadata


def get_url() -> str:
    # set programmatically by scripts/gen_migration.py for a throwaway db
    url = config.get_alembic_option("sqlalchemy.url")
    if url:
        return url
    # for running migrations against the real db: alembic needs a sync
    # driver, unlike the app's own aiosqlite engine.
    return f"sqlite:///{settings.SQLITE_PATH}"


def run_migrations() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # batch mode: sqlite can't ALTER most column properties in place,
        # alembic works around it by recreating the table.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


run_migrations()
