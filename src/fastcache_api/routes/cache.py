from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import service
from ..db import get_session
from ..models import CachePublic, CacheRequest, CachesPublic

router = APIRouter(
    prefix="/caches",
    tags=["caches"],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/", response_model=CachesPublic)
async def get_caches(session: SessionDep) -> CachesPublic:
    return await service.list_caches(session)


@router.get("/{cache_id}", response_model=CachePublic)
async def get_cache(cache_id: UUID, session: SessionDep) -> CachePublic:
    return await service.get_cache(session, cache_id)


@router.post("/", response_model=CachePublic, status_code=status.HTTP_201_CREATED)
async def create_cache(
    req: CacheRequest,
    session: SessionDep,
    response: Response,
) -> CachePublic:
    result = await service.create_cache(session, req)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result.cache


@router.delete("/{cache_id}", response_model=CachePublic)
async def shutdown_cache(
    cache_id: UUID, session: SessionDep, background_tasks: BackgroundTasks
) -> CachePublic:
    return await service.shutdown_cache(session, background_tasks, cache_id)
