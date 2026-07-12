"""Pure decision logic: no IO, no DB, no subprocess access."""

from collections.abc import Iterable

from .models import CacheConfig, CacheState

NON_FINAL_STATES: list[str] = [s.value for s in CacheState if not s.is_final()]


def decide_final_state(exit_code: int | None) -> CacheState:
    return CacheState.completed if exit_code == 0 else CacheState.failed


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
