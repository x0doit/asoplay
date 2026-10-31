"""
Правило 30 дней по расписанию: раз в сутки auto-«Смотрю» после 30 дней простоя уходит в «Брошено».

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Сам проход — sweep_dropped_after_30_days в core/functions/lists.py. Здесь — когда он идёт:
    - раз в сутки в AV_LISTS_SWEEP_AT: время UTC «ЧЧ:ММ», по умолчанию 04:00; off — не по расписанию;
    - при старте сервера — сразу, если назначенный проход пропущен (сервер лежал) или оборвался;
    - под замком GET_LOCK — два процесса не идут одновременно; отметка в aviev_task_runs — не дважды за срок,
      даже при перезапусках и сдвиге часов.
Каждая пачка сразу после commit уходит вкладкам человека кадром «lists» (core/functions/realtime.py).
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from core.classes.clock import utc_now
from core.classes.db import connect
from core.config import config
from core.functions import lists, realtime

log = logging.getLogger("asoplay.lists_sweep")

NAME = "lists_sweep"
DEFAULT_AT = (4, 0)
RETRY_AFTER = 600  # сбой прохода (база недоступна) — повтор через 10 минут, а не через сутки

_task: asyncio.Task | None = None
_stop = threading.Event()


def sweep_at() -> tuple[int, int] | None:
    """Время прохода из AV_LISTS_SWEEP_AT; off — None. Не «ЧЧ:ММ» — ошибка в журнал и 04:00."""
    raw = config.lists_sweep_at()
    if raw.lower() == "off":
        return None
    found = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", raw)
    if not found:
        log.error("AV_LISTS_SWEEP_AT=%r — не «ЧЧ:ММ»; проход в 04:00 UTC", raw)
        return DEFAULT_AT
    return int(found.group(1)), int(found.group(2))


def last_slot(now: datetime, at: tuple[int, int]) -> datetime:
    """Последнее назначенное время прохода, не позже now."""
    slot = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def seconds_to_next(now: datetime, at: tuple[int, int]) -> float:
    """Сколько ждать до следующего назначенного прохода."""
    return (last_slot(now, at) + timedelta(days=1) - now).total_seconds()


def announce(entries: list[tuple[int, dict[str, Any]]]) -> None:
    """Пачка перенесена: каждому человеку — кадр «lists» со своими записями во все его вкладки."""
    by_user: dict[int, list[dict[str, Any]]] = {}
    for user_id, entry in entries:
        by_user.setdefault(user_id, []).append(entry)
    for user_id, items in by_user.items():
        realtime.lists_changed(user_id, items)


def run_once(now: datetime | None = None, *, name: str = NAME, at: tuple[int, int] = DEFAULT_AT,
             batch: int = lists.SWEEP_BATCH,
             on_batch: Callable[[list[tuple[int, dict[str, Any]]]], None] | None = None) -> int | None:
    """Проход, если он нужен; вернёт, сколько перенесено. None — прохода не было: он идёт в другом процессе
    или уже прошёл после последнего назначенного времени."""
    now = now or utc_now()
    lock = f"asoplay:{name}"
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, 0)", (lock,))
        if (cur.fetchone() or (0,))[0] != 1:
            log.info("Проход %s уже идёт в другом процессе", name)
            return None
        try:
            cur.execute("SELECT started_at, finished_at FROM aviev_task_runs WHERE name = %s", (name,))
            row = cur.fetchone()
            if row and row[1] is not None and row[0] >= last_slot(now, at):
                return None
            started = time.monotonic()
            cur.execute("INSERT INTO aviev_task_runs (name, started_at, finished_at, processed)"
                        " VALUES (%s, %s, NULL, 0) ON DUPLICATE KEY UPDATE"
                        " started_at = VALUES(started_at), finished_at = NULL, processed = 0", (name, now))
            conn.commit()
            moved = lists.sweep_dropped_after_30_days(conn, now, batch=batch, on_batch=on_batch, stop=_stop.is_set)
            if _stop.is_set():
                log.info("Проход %s прерван остановкой сервера: перенесено %s, остальное — при старте", name, moved)
                return moved
            cur.execute("UPDATE aviev_task_runs SET finished_at = %s, processed = %s WHERE name = %s",
                        (utc_now(), moved, name))
            conn.commit()
            log.info("Проход %s: в «Брошено» перенесено %s за %.1f с", name, moved, time.monotonic() - started)
            return moved
        finally:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock,))
            except Exception:  # noqa: BLE001 — соединение умерло: замок снят вместе с ним
                log.warning("Could not release the lock %s", lock)


async def _loop(at: tuple[int, int], sleep: Callable[[float], Any] = asyncio.sleep) -> None:
    """Проход сразу (догнать пропущенный), потом — в каждое назначенное время."""
    while True:
        wait = None
        try:
            await asyncio.to_thread(run_once, at=at, on_batch=announce)
        except Exception:  # noqa: BLE001 — сбой прохода не роняет сервер и не отменяет следующий
            log.exception("Проход правила 30 дней не удался")
            wait = RETRY_AFTER
        delay = seconds_to_next(utc_now(), at)
        await sleep(min(delay, wait) if wait else delay)


async def startup() -> None:
    """Запустить расписание (из core.py при старте сервера)."""
    global _task
    at = sweep_at()
    if at is None:
        log.info("Правило 30 дней по расписанию выключено (AV_LISTS_SWEEP_AT=off)")
        return
    if _task is None:
        _stop.clear()
        _task = asyncio.create_task(_loop(at))


async def shutdown() -> None:
    """Остановить расписание; проход в потоке остановится после текущей пачки."""
    global _task
    _stop.set()
    if _task is not None:
        _task.cancel()
        _task = None
