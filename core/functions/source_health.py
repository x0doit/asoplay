# -*- coding: utf-8 -*-
"""
In-memory source health state.

This is intentionally process-local. It protects the running app from a source
that is timing out or throwing repeatedly, without changing the database or
publishing any persistent operational state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Iterable


FAIL_THRESHOLD = 3
COOLDOWN_SECONDS = 60.0
MAX_ERROR_LENGTH = 180
# Окно для «Обзора» в панели Anime Social: доля ошибок и задержка за последние сутки, по часам
WINDOW_HOURS = 24


@dataclass
class SourceState:
    name: str
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_ok_at: float = 0.0
    last_error_at: float = 0.0
    last_error: str = ""
    latency_ms: int | None = None
    # номер часа → [успехи, ошибки, сумма задержек успехов в мс]
    hours: dict[int, list[int]] = field(default_factory=dict)

    def _hour(self) -> list[int]:
        now = int(time.time() // 3600)
        for old in [h for h in self.hours if h <= now - WINDOW_HOURS]:
            del self.hours[old]
        return self.hours.setdefault(now, [0, 0, 0])


_STATES: dict[str, SourceState] = {}


def _state(source: str) -> SourceState:
    source = str(source or "").strip()
    if source not in _STATES:
        _STATES[source] = SourceState(name=source)
    return _STATES[source]


def is_available(source: str) -> bool:
    return _state(source).cooldown_until <= time.time()


def cooldown_remaining(source: str) -> int:
    remaining = _state(source).cooldown_until - time.time()
    return max(0, int(round(remaining)))


def mark_success(source: str, latency_ms: int | None = None) -> None:
    state = _state(source)
    state.successes += 1
    state.consecutive_failures = 0
    state.cooldown_until = 0.0
    state.last_ok_at = time.time()
    state.latency_ms = latency_ms
    hour = state._hour()
    hour[0] += 1
    hour[2] += max(0, latency_ms or 0)


def mark_failure(source: str, error: object) -> None:
    state = _state(source)
    state.failures += 1
    state.consecutive_failures += 1
    state._hour()[1] += 1
    state.last_error_at = time.time()
    msg = str(error or "").replace("\n", " ").strip()
    state.last_error = msg[:MAX_ERROR_LENGTH]
    if state.consecutive_failures >= FAIL_THRESHOLD:
        state.cooldown_until = time.time() + COOLDOWN_SECONDS


def available_sources(sources: Iterable[str]) -> list[str]:
    return [source for source in sources if is_available(source)]


def snapshot(sources: Iterable[str] | None = None) -> dict[str, dict[str, object]]:
    names = list(sources) if sources is not None else sorted(_STATES)
    now = time.time()
    out: dict[str, dict[str, object]] = {}
    for name in names:
        state = _state(name)
        remaining = max(0, int(round(state.cooldown_until - now)))
        out[name] = {
            "available": remaining <= 0,
            "cooldown_remaining": remaining,
            "successes": state.successes,
            "failures": state.failures,
            "consecutive_failures": state.consecutive_failures,
            "last_ok_at": state.last_ok_at or None,
            "last_error_at": state.last_error_at or None,
            "last_error": state.last_error or None,
            "latency_ms": state.latency_ms,
        }
    return out


def window(source: str) -> dict[str, object]:
    """За последние WINDOW_HOURS часов: сколько было запросов, доля ошибок и средняя задержка успешных."""
    state = _state(source)
    state._hour()
    ok = sum(h[0] for h in state.hours.values())
    failed = sum(h[1] for h in state.hours.values())
    latency = sum(h[2] for h in state.hours.values())
    total = ok + failed
    return {
        "requests_24h": total,
        "fail_rate_24h": round(failed / total, 3) if total else None,
        "latency_ms": round(latency / ok) if ok else None,
    }
