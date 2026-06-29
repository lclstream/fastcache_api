import contextlib
import logging
import socket
import subprocess
from collections.abc import Iterable
from uuid import UUID

import psutil

from .config import settings
from .models import CacheConfig, CacheProcess, FastcacheConfig

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


def start_cache(cache_id: UUID, config: CacheConfig) -> CacheProcess:
    run_dir = settings.RUN_DIR / str(cache_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = run_dir / "config.json"
    config_path.write_text(
        FastcacheConfig.from_cache_config(config).model_dump_json(indent=2)
    )

    log_path = (run_dir / "cache.log").resolve()
    with log_path.open("ab") as log_file:
        proc = subprocess.Popen(
            [settings.FASTCACHE_BINARY, config_path],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    # (pid, create_time) is a reuse-safe identity; fail fast if the process died
    # immediately.
    try:
        create_time = psutil.Process(proc.pid).create_time()
    except psutil.Error as exc:
        raise RuntimeError(
            f"Cache {cache_id} (pid={proc.pid}) exited immediately after launch; "
            f"check logs at {log_path}"
        ) from exc
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


def stop_cache(pid: int, create_time: float | None, timeout: float = 5.0) -> None:
    """SIGTERM the cache process tree, then SIGKILL after ``timeout``.

    No-ops unless the identity matches, so a recycled pid is never killed.
    """
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
