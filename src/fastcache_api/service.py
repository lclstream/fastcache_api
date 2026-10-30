from uuid import UUID, uuid4

from fastapi import BackgroundTasks
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import process, repo
from .config import settings
from .core import allocate_port_pair, ports_in_use
from .db import SessionLocal
from .exceptions import (
    CacheKeyConflict,
    CacheNotFound,
    CachePortsExhausted,
    CacheStartFailed,
)
from .lifecycle import schedule_exit_watch
from .models import CacheConfig, CachePublic, CacheRequest, CachesPublic, CacheState
from .tables import Cache


class CacheCreationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    cache: CachePublic
    created: bool


async def _resolve_active_by_key(session: AsyncSession, key: str) -> Cache | None:
    """Find a live cache for `key`.

    A dead cache found here hasn't been reconciled yet (the exit watcher or
    sweep haven't run), so it's finalized here and treated as absent.
    """
    candidate = await repo.find_active_by_key(session, key)
    if candidate is None:
        return None
    if process.is_alive(candidate.pid, candidate.create_time):
        return candidate
    repo.finalize_cache(candidate, process.exit_code(candidate.pid))
    await session.commit()
    return None


async def get_cache(session: AsyncSession, cache_id: UUID) -> CachePublic:
    cache = await repo.get_cache(session, cache_id)
    if cache is None:
        raise CacheNotFound(f"cache {cache_id} not found")
    return CachePublic.model_validate(cache)


async def list_caches(session: AsyncSession) -> CachesPublic:
    caches = await repo.list_caches(session)
    return CachesPublic(caches=[CachePublic.model_validate(c) for c in caches])


async def create_cache(session: AsyncSession, req: CacheRequest) -> CacheCreationResult:
    existing = await _resolve_active_by_key(session, req.key)
    if existing is not None:
        return CacheCreationResult(
            cache=CachePublic.model_validate(existing), created=False
        )

    # The cache always runs as a local subprocess of this api server, so the
    # ZMQ URIs are published under this host's canonical (FQDN) name.
    hostname = process.canonical_hostname()

    in_use = ports_in_use(await repo.list_active_configs(session))
    try:
        pull_port, push_port = allocate_port_pair(
            in_use, settings.CACHE_PORT_START, settings.CACHE_PORT_END
        )
    except RuntimeError as exc:
        raise CachePortsExhausted(str(exc)) from exc

    timeout = (
        req.idle_timeout_ms
        if req.idle_timeout_ms is not None
        else CacheConfig.model_fields["timeout"].default
    )
    config = CacheConfig(
        hostname=hostname,
        pull_uri=f"tcp://{hostname}:{pull_port}",
        push_uri=f"tcp://{hostname}:{push_port}",
        timeout=timeout,
        type=req.output.fastcache_type,
    )

    cache_id = uuid4()
    try:
        proc = await process.start_cache(cache_id, config, req.log_path)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise CacheStartFailed(f"Failed to start cache process: {exc}") from exc

    cache = Cache(
        id=cache_id,
        key=req.key,
        pid=proc.pid,
        create_time=proc.create_time,
        user=req.requested_by,
        state=CacheState.active,
        log_path=str(proc.log_path),
        config=config.model_dump(mode="json"),
    )
    repo.insert_cache(session, cache)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Lost a race with a concurrent request for the same key: don't
        # leave our process running, join the winner instead.
        await process.stop_cache(proc.pid, proc.create_time, timeout=0)
        winner = await _resolve_active_by_key(session, req.key)
        if winner is None:
            raise CacheKeyConflict(f"Cache key '{req.key}' conflict") from exc
        return CacheCreationResult(
            cache=CachePublic.model_validate(winner), created=False
        )

    await session.refresh(cache)
    schedule_exit_watch(cache.id, proc.pid)
    return CacheCreationResult(cache=CachePublic.model_validate(cache), created=True)


async def _teardown_and_free_key(
    cache_id: UUID, pid: int, create_time: float | None
) -> None:
    await process.stop_cache(pid, create_time, settings.SHUTDOWN_GRACE_SECONDS)
    # Only free the key once the process is confirmed stopped.
    async with SessionLocal() as session:
        cache = await repo.get_cache(session, cache_id)
        if cache is not None:
            cache.key = None
            await session.commit()


async def shutdown_cache(
    session: AsyncSession, background_tasks: BackgroundTasks, cache_id: UUID
) -> CachePublic:
    cache = await repo.get_cache(session, cache_id)
    if cache is None:
        raise CacheNotFound(f"cache {cache_id} not found")

    cache.state = CacheState.canceled
    await session.commit()
    await session.refresh(cache)

    # Tear down the process tree in the background so DELETE returns promptly.
    background_tasks.add_task(
        _teardown_and_free_key, cache.id, cache.pid, cache.create_time
    )
    return CachePublic.model_validate(cache)
