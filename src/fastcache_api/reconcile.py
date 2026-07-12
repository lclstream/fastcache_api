import asyncio
import logging
from uuid import UUID

import anyio

from . import repo
from .config import settings
from .db import SessionLocal
from .models import CacheState
from .process import exit_code, is_alive, wait_exit

logger = logging.getLogger(__name__)


async def sweep_dead_caches() -> int:
    """Reconcile non-final caches whose process is gone; return how many changed.

    Caches outlive the api server, so a row's state is a claim we verify against
    the live process. Exit code 0 -> completed, else (or unknown, e.g. after an
    api restart) -> failed.
    """
    async with SessionLocal() as session:
        caches = await repo.list_non_final(session)

        stale = 0
        for cache in caches:
            if is_alive(cache.pid, cache.create_time):
                continue
            ec = exit_code(cache.pid)
            repo.finalize_cache(cache, ec)
            logger.warning(
                "Cache %s (pid=%d) is no longer running (exit_code=%s); marking %s",
                cache.id,
                cache.pid,
                ec,
                cache.state,
            )
            stale += 1

        if stale:
            await session.commit()
        return stale


async def reconcile_caches() -> None:
    """One-shot sweep at startup to reconcile DB state against live processes."""
    stale = await sweep_dead_caches()
    logger.info("Startup reconcile complete; %d cache(s) reconciled", stale)


async def watch_and_record(cache_id: UUID, pid: int) -> None:
    exit_code = await wait_exit(pid)
    if exit_code is None:
        return
    # we shield from app shutdown - we will lose our db connection
    # and this won't be able to commit properly
    with anyio.CancelScope(shield=True):
        async with SessionLocal() as session:
            cache = await repo.get_cache(session, cache_id)
            if cache is None or CacheState(cache.state).is_final():
                return
            repo.finalize_cache(cache, exit_code)
            await session.commit()
    logger.info(
        "Cache %s (pid=%d) exited (code=%s); marked %s",
        cache_id,
        pid,
        exit_code,
        cache.state,
    )


async def monitor_caches() -> None:
    """Poll liveness until cancelled; one failed sweep must not kill the loop."""
    while True:
        await asyncio.sleep(settings.CACHE_POLL_SECONDS)
        try:
            await sweep_dead_caches()
        except Exception:
            logger.exception("Cache liveness sweep failed; continuing")
