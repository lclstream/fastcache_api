import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

import anyio
import anyio.abc

from .reconcile import watch_and_record

logger = logging.getLogger(__name__)

_task_group: anyio.abc.TaskGroup | None = None


def schedule_exit_watch(cache_id: UUID, pid: int) -> None:
    """Eagerly await a just-spawned cache's exit and persist its outcome.

    sweep_dead_caches remains the fallback for caches whose watch task was lost
    (e.g. api restart).
    """
    if _task_group is None:
        return
    _task_group.start_soon(watch_and_record, cache_id, pid)


@asynccontextmanager
async def exit_watchers() -> AsyncGenerator[None]:
    """Hold the task group that per-cache exit-watch tasks run in."""
    global _task_group
    async with anyio.create_task_group() as tg:
        _task_group = tg
        try:
            yield
        finally:
            tg.cancel_scope.cancel()
            _task_group = None
