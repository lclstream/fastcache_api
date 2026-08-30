import json
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import AnyUrl, BaseModel, ConfigDict


class CacheState(StrEnum):
    new = "new"
    queued = "queued"
    active = "active"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"

    def is_final(self) -> bool:
        return self.value in ("completed", "failed", "canceled")


class CacheOutput(StrEnum):
    """fastcache's outgoing socket, and what a consumer must dial.

    Values map to the ``type`` field of fastcache's config (see its README).
    ``router`` and ``rep`` are request-driven: the consumer asks for each
    message rather than being pushed to. Metrics only work under ``push``.
    """

    push = "push"  # fastcache type 4; consumer connects PULL
    router = "router"  # type 5; consumer connects DEALER
    rep = "rep"  # type 6; consumer connects REQ

    @property
    def fastcache_type(self) -> int:
        return _FASTCACHE_TYPE[self]


_FASTCACHE_TYPE = {
    CacheOutput.push: 4,
    CacheOutput.router: 5,
    CacheOutput.rep: 6,
}


class CacheConfig(BaseModel):
    """Configuration document persisted as JSON on a cache row.

    Mirrors the cacheserver config (see config/default.json) plus the
    resolved ZMQ bind URIs. ``to_fastcache_json`` renders the on-disk config
    the fastcache binary consumes (see src/config.cpp): the ZMQ URIs are
    renamed to ``inurl``/``outurl`` and ``hostname`` is dropped.
    """

    hostname: str
    pull_uri: AnyUrl
    push_uri: AnyUrl
    type: int = 4
    helper_threads: int = 0
    io_threads: int = 16
    hwm: int = 10
    # Default to 2 minutes for now.
    timeout: int = 120_000
    verbose: bool = False

    def to_fastcache_json(self, indent: int = 2) -> str:
        """Render the config consumed by the fastcache binary."""
        data = self.model_dump(
            mode="json", exclude={"hostname", "pull_uri", "push_uri"}
        )
        return json.dumps(
            {"inurl": str(self.pull_uri), "outurl": str(self.push_uri), **data},
            indent=indent,
        )


class CacheRequest(BaseModel):
    # The cache's dedup/lookup identity. Could be per-transfer (transfer ID)
    # or per-experiment (experiment name)
    key: str
    # Human who initiated the transfer upstream (bearer token is a shared
    # service identity, so attribution must travel in the request body).
    requested_by: str
    # Absolute path the orchestrator dictates for this cache's log.
    log_path: Path
    # Override for CacheConfig.timeout (fastcache's idle-receive timeout, ms).
    idle_timeout_ms: int | None = None
    # Outgoing socket pattern; decides what the consumer connects with.
    output: CacheOutput = CacheOutput.push


class CachePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str | None
    user: str
    state: CacheState
    exit_code: int | None
    log_path: Path
    config: CacheConfig


class CacheProcess(BaseModel):
    pid: int
    create_time: float | None
    log_path: Path


class CachesPublic(BaseModel):
    caches: list[CachePublic]
