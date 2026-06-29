from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..dependencies import TokenPayload, require_user
from ..models import (
    CacheConfig,
    CachePublic,
    CacheRequest,
    CachesPublic,
    CacheState,
)
from ..process import (
    allocate_port_pair,
    canonical_hostname,
    ports_in_use,
    start_cache,
    stop_cache,
)
from ..tables import Cache

router = APIRouter(
    prefix="/caches",
    tags=["caches"],
    dependencies=[Depends(require_user)],
)


@router.get("/", response_model=CachesPublic)
async def get_caches(
    session: Annotated[AsyncSession, Depends(get_session)],
):
    result = await session.execute(select(Cache))
    caches = result.scalars().all()
    return CachesPublic(caches=[CachePublic.model_validate(c) for c in caches])


@router.get("/{cache_id}", response_model=CachePublic)
async def get_cache(
    cache_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    cache = await session.get(Cache, cache_id)
    if cache is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cache not found"
        )
    return cache


@router.post("/", response_model=CachePublic, status_code=status.HTTP_201_CREATED)
async def create_cache(
    req: CacheRequest,
    user: Annotated[TokenPayload, Depends(require_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    # The cache always runs as a local subprocess of this api server, so the
    # ZMQ URIs are published under this host's canonical (FQDN) name.
    hostname = canonical_hostname()

    # Derive the ports already bound by live caches and allocate the next free pair.
    active = await session.execute(
        select(Cache).where(
            Cache.state.in_([s.value for s in CacheState if not s.is_final()])
        )
    )
    in_use = ports_in_use(
        CacheConfig.model_validate(cache.config) for cache in active.scalars().all()
    )
    try:
        pull_port, push_port = allocate_port_pair(
            in_use, settings.CACHE_PORT_START, settings.CACHE_PORT_END
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    config = CacheConfig(
        hostname=hostname,
        pull_uri=f"tcp://{hostname}:{pull_port}",
        push_uri=f"tcp://{hostname}:{push_port}",
    )

    cache_id = uuid4()
    try:
        proc = start_cache(cache_id, config)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to start cache process: {exc}",
        ) from exc

    cache = Cache(
        id=cache_id,
        transfer_id=req.transfer_id,
        pid=proc.pid,
        create_time=proc.create_time,
        user=req.requested_by,
        state=CacheState.active,
        log_path=str(proc.log_path),
        config=config.model_dump(mode="json"),
    )
    session.add(cache)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Don't leave an orphaned process behind for the rejected request.
        stop_cache(proc.pid, proc.create_time, timeout=0)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Cache with transfer_id '{req.transfer_id}' already exists"),
        ) from exc

    await session.refresh(cache)
    return cache


@router.delete("/{cache_id}", response_model=CachePublic)
async def shutdown_cache(
    cache_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    background_tasks: BackgroundTasks,
):
    cache = await session.get(Cache, cache_id)
    if cache is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cache not found"
        )

    cache.state = CacheState.canceled
    await session.commit()
    await session.refresh(cache)

    # Tear down the process tree in the background so DELETE returns promptly.
    background_tasks.add_task(
        stop_cache, cache.pid, cache.create_time, settings.SHUTDOWN_GRACE_SECONDS
    )
    return cache
