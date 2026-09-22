import contextlib
import logging
import socket
import subprocess
from pathlib import Path
from uuid import UUID

import anyio
import anyio.abc
import psutil

from .config import settings
from .models import CacheConfig, CacheProcess

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"
CACHE_LOG_FILENAME = "cache.log"
# Traversable + readable by everyone
RUN_DIR_MODE = 0o755
CACHE_LOG_MODE = 0o644
CONFIG_MODE = 0o600


def canonical_hostname() -> str:
    """This host's preferred public name for cache ZMQ URIs."""
    return (socket.getfqdn() or socket.gethostname()).lower()


# Live anyio Process handles for children we spawned
_processes: dict[int, anyio.abc.Process] = {}


def exit_code(pid: int) -> int | None:
    """Exit code of a pid we spawned. None if unknown to us or still running."""
    proc = _processes.get(pid)
    if proc is None:
        return None
    return proc.returncode


async def wait_exit(pid: int) -> int | None:
    """Await a pid we spawned exiting. None if unknown to us."""
    proc = _processes.get(pid)
    if proc is None:
        return None
    exit_code = await proc.wait()
    _processes.pop(pid, None)
    return exit_code


def ensure_cache_root() -> Path:
    """The run-dir tree, created and opened up. Ours to own, not the caller's.

    chmod is explicit because the unit runs with UMask=0077, which would
    otherwise leave everything owner-only and unreadable to the users
    lclstream_api serves these logs to.
    """
    root = settings.CACHE_LOG_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(RUN_DIR_MODE)
    return root


async def start_cache(cache_id: UUID, config: CacheConfig) -> CacheProcess:
    """Spawn a cache, with its run dir under the tree this service owns."""
    run_dir = ensure_cache_root() / str(cache_id)
    run_dir.mkdir(exist_ok=True)
    run_dir.chmod(RUN_DIR_MODE)

    config_path = run_dir / CONFIG_FILENAME
    config_path.write_text(config.to_fastcache_json())
    config_path.chmod(CONFIG_MODE)

    log_path = run_dir / CACHE_LOG_FILENAME
    # Created before the child so the mode is set even on an instant exit.
    log_path.touch()
    log_path.chmod(CACHE_LOG_MODE)

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
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
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
