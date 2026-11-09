"""
Здоровье источников видео в памяти процесса: успехи, сбои, задержка и остывание после череды сбоев.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Состояние нарочно живёт только в процессе: оно защищает работающий сайт от источника, который раз за разом не
отвечает или падает, не трогая базу и не публикуя постоянного состояния. Источник остывает COOLDOWN_SECONDS секунд
после FAIL_THRESHOLD сбоев связи подряд (таймауты, ошибки httpx, ответы 5xx и 429) или после ошибок разбора у
FAIL_THRESHOLD разных тайтлов подряд: один кривой тайтл источник не остужает, сломанный разбор у всех — остужает.
Быстрый отказ (ответ 4xx) не остужает. Успех сбрасывает обе серии. Все сбои идут в счётчики; для «Обзора» в панели
Anime Social — доля ошибок и задержка за последние сутки, по часам.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Iterable

import httpx


FAIL_THRESHOLD = 3
COOLDOWN_SECONDS = 60.0
MAX_ERROR_LENGTH = 180
# Окно для «Обзора» в панели Anime Social: доля ошибок и задержка за последние сутки, по часам
WINDOW_HOURS = 24


@dataclass
class SourceState:
    """Состояние источника: счётчики, остывание, последние успех и ошибка, задержка, почасовые
    итоги за сутки."""

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
    # тайтлы с ошибкой разбора с последнего успеха — не больше FAIL_THRESHOLD
    parse_titles: set[object] = field(default_factory=set)

    def _hour(self) -> list[int]:
        """Итоги текущего часа [успехи, ошибки, сумма задержек]; часы старше суток выбрасываются."""
        now = int(time.time() // 3600)
        for old in [h for h in self.hours if h <= now - WINDOW_HOURS]:
            del self.hours[old]
        return self.hours.setdefault(now, [0, 0, 0])


_STATES: dict[str, SourceState] = {}


class SourceDown(Exception):
    """Сбой связи или источника, пришедший не исключением httpx: клиент API библиотеки прячет его в ответ
    (core/functions/source_episodes.py)."""


class SourceRefused(Exception):
    """Отказ 4xx, пришедший не исключением httpx (клиент API библиотеки)."""


def kind(error: object) -> str:
    """Вид сбоя: "outage" — связь или сам источник: таймаут (он приходит и текстом), ошибка httpx, ответ 5xx или 429,
    SourceDown; "refusal" — быстрый отказ: ответ 4xx, SourceRefused; "parse" — остальное, ошибка разбора ответа."""
    if not isinstance(error, BaseException):
        return "outage"
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return "outage" if code >= 500 or code == 429 else "refusal"
    if isinstance(error, SourceRefused):
        return "refusal"
    if isinstance(error, (httpx.HTTPError, asyncio.TimeoutError, TimeoutError, OSError, SourceDown)):
        return "outage"
    return "parse"


def _state(source: str) -> SourceState:
    """Состояние источника по имени; нет такого — создаётся."""
    source = str(source or "").strip()
    if source not in _STATES:
        _STATES[source] = SourceState(name=source)
    return _STATES[source]


def is_available(source: str) -> bool:
    """Источник сейчас не остывает."""
    return _state(source).cooldown_until <= time.time()


def cooldown_remaining(source: str) -> int:
    """Сколько секунд источнику ещё остывать; 0 — не остывает."""
    remaining = _state(source).cooldown_until - time.time()
    return max(0, int(round(remaining)))


def mark_success(source: str, latency_ms: int | None = None) -> None:
    """Засчитать успех: обе серии сбоев и остывание сбрасываются, задержка — в почасовые итоги."""
    state = _state(source)
    state.successes += 1
    state.consecutive_failures = 0
    state.parse_titles.clear()
    state.cooldown_until = 0.0
    state.last_ok_at = time.time()
    state.latency_ms = latency_ms
    hour = state._hour()
    hour[0] += 1
    hour[2] += max(0, latency_ms or 0)


def mark_failure(source: str, error: object, title: str | None = None) -> None:
    """Засчитать сбой с текстом ошибки; по его виду (kind) — в серию:
    - сбой связи или источника — FAIL_THRESHOLD подряд дают остывание на COOLDOWN_SECONDS;
    - ошибка разбора — в свою серию, где тайтл (title) считается один раз: остывание — после FAIL_THRESHOLD разных
      тайтлов. Без тайтла каждая ошибка — как новый тайтл;
    - быстрый отказ — ни в какую серию."""
    state = _state(source)
    state.failures += 1
    state._hour()[1] += 1
    state.last_error_at = time.time()
    msg = str(error or "").replace("\n", " ").strip()
    state.last_error = msg[:MAX_ERROR_LENGTH]
    what = kind(error)
    if what == "refusal":
        return
    if what == "parse":
        if len(state.parse_titles) < FAIL_THRESHOLD:
            state.parse_titles.add(object() if title is None else title)
        streak = len(state.parse_titles)
    else:
        state.consecutive_failures += 1
        streak = state.consecutive_failures
    if streak >= FAIL_THRESHOLD:
        state.cooldown_until = time.time() + COOLDOWN_SECONDS


def available_sources(sources: Iterable[str]) -> list[str]:
    """Источники из списка, которые сейчас не остывают."""
    return [source for source in sources if is_available(source)]


def snapshot(sources: Iterable[str] | None = None) -> dict[str, dict[str, object]]:
    """Состояние источников для /health и /source-health: доступен ли, сколько остывать, счётчики, последние успех
    и ошибка, задержка."""
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
