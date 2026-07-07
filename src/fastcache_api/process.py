import contextlib
import logging
import socket
import subprocess
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

import anyio
import anyio.abc
import psutil

from .config import settings
from .models import CacheConfig, CacheProcess

logger = logging.getLogger(__name__)


def canonical_hostname() -> str:
    """This host's preferred public name for cache ZMQ URIs."""
    return (socket.getfqdn() or socket.gethostname()).lower()


def ports_in_use(configs: Iterable[CacheConfig]) -> set[int]:
    used: set[int] = set()
    for config in configs:
        for uri in (config.pull_uri, config.push_uri):
            if uri.port is not None:
                used.add(uri.port)
    return used


def allocate_port_pair(in_use: set[int], start: int, end: int) -> tuple[int, int]:
    for pull in range(start, end + 1, 2):
        push = pull + 1
        if push <= end and pull not in in_use and push not in in_use:
            return pull, push
    raise RuntimeError(f"no free cache port pair in range [{start}, {end}]")


# Live anyio Process handles for children we spawned
_processes: dict[int, anyio.abc.Process] = {}


async def start_cache(
    cache_id: UUID, config: CacheConfig, log_path: Path
) -> CacheProcess:
    run_dir = log_path.parent
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = run_dir / "config.json"
    config_path.write_text(config.to_fastcache_json())

    log_path = log_path.resolve()
    with log_path.open("ab") as log_file:
        proc = await anyio.open_process(
            [settings.FASTCACHE_BINARY, config_path],
            cwd=run_dir,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        create_time = psutil.Process(proc.pid).create_time()
    except psutil.Error as exc:
        raise RuntimeError(
            f"Cache {cache_id} (pid={proc.pid}) exited immediately after launch; "
            f"check logs at {log_path}"
        ) from exc
    _processes[proc.pid] = proc
    logger.info("Started cache %s (pid=%d)", cache_id, proc.pid)
    return CacheProcess(pid=proc.pid, create_time=create_time, log_path=log_path)


# Tight on purpose: a false match would kill a recycled pid's unrelated process.
_CREATE_TIME_TOLERANCE = 1e-3


def resolve_process(pid: int, create_time: float | None) -> psutil.Process | None:
    """Return the live process iff (pid, create_time) still matches; else None."""
    if create_time is None:
        return None
    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - create_time) <= _CREATE_TIME_TOLERANCE:
            return proc
    except psutil.Error:
        return None
    return None


def is_alive(pid: int, create_time: float | None) -> bool:
    return resolve_process(pid, create_time) is not None


async def stop_cache(pid: int, create_time: float | None, timeout: float = 5.0) -> None:
    """Terminate the cache process, escalating to SIGKILL after ``timeout``.

    Uses anyio Process handle if we still have one; falls back to psutil by
    (pid, create_time) identity for orphaned/cross-restart cases.
    No-ops unless the identity matches, so a recycled pid is never killed.
    """
    proc = _processes.get(pid)
    if proc is not None:
        proc.terminate()
        with anyio.move_on_after(timeout):
            await proc.wait()
        if proc.returncode is None:
            logger.warning(
                "Cache pid=%d still alive after %.1fs; sending SIGKILL", pid, timeout
            )
            proc.kill()
            await proc.wait()
        _processes.pop(pid, None)
        return

    parent = resolve_process(pid, create_time)
    if parent is None:
        logger.info(
            "Cache pid=%d gone or identity mismatch (pid reuse?); nothing to kill",
            pid,
        )
        return

    procs = [parent, *parent.children(recursive=True)]
    for proc in procs:
        with contextlib.suppress(psutil.NoSuchProcess):
            proc.terminate()

    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        logger.warning(
            "Cache pid=%d still alive after %.1fs; sending SIGKILL", proc.pid, timeout
        )
        with contextlib.suppress(psutil.NoSuchProcess):
            proc.kill()
    psutil.wait_procs(alive, timeout=timeout)
