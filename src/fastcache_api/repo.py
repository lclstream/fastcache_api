from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .core import NON_FINAL_STATES, decide_final_state
from .models import CacheConfig
from .tables import Cache


async def get_cache(session: AsyncSession, cache_id: UUID) -> Cache | None:
    return await session.get(Cache, cache_id)


async def list_caches(session: AsyncSession) -> Sequence[Cache]:
    result = await session.execute(select(Cache))
    return result.scalars().all()


async def list_non_final(session: AsyncSession) -> Sequence[Cache]:
    result = await session.execute(
        select(Cache).where(Cache.state.in_(NON_FINAL_STATES))
    )
    return result.scalars().all()


async def find_active_by_key(session: AsyncSession, key: str) -> Cache | None:
    result = await session.execute(
        select(Cache).where(Cache.key == key, Cache.state.in_(NON_FINAL_STATES))
    )
    return result.scalar_one_or_none()


async def list_active_configs(session: AsyncSession) -> list[CacheConfig]:
    caches = await list_non_final(session)
    return [CacheConfig.model_validate(cache.config) for cache in caches]


def insert_cache(session: AsyncSession, cache: Cache) -> None:
    session.add(cache)


def finalize_cache(cache: Cache, exit_code: int | None) -> None:
    """Set `cache` to its terminal state for `exit_code` and free its key.

    Does not commit; caller owns the transaction boundary.
    """
    cache.state = decide_final_state(exit_code)
    cache.exit_code = exit_code
    cache.key = None
