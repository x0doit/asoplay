"""
Запасной пакет присутствия для Anime Social: POST /clients/presence раз в минуту, пока нет соединения сервер–сервер.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Соцсети присутствие сообщает соединение сервер–сервер (core/classes/as_realtime.py) в момент изменения; пока его
нет, раз в REPORT_EVERY секунд уходит пакет: кто сейчас на AsoPlay и, с согласия, что смотрит. Соцсеть принимает
только тех, у кого есть сессия AsoPlay в ней. Нужен ли пакет, отвечает batch_needed — его ставит realtime.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from core.classes import as_api, as_sessions
from core.config.config import SITE_URL
from core.functions import presence, privacy as privacy_rules

log = logging.getLogger("asoplay.presence")

REPORT_EVERY = 60


def build_batch() -> dict[str, Any]:
    """Тело POST /clients/presence: кто сейчас на AsoPlay и, с согласия, что смотрит."""
    marks = presence.presence.current()
    privacy = privacy_rules.for_users(marks)
    shown = {uid: m for uid, m in marks.items() if not privacy.get(uid, privacy_rules.OPEN).hide_presence}
    titles = presence.title_cards(m.mal_id for uid, m in shown.items() if privacy.get(uid, privacy_rules.OPEN).show_watching)
    watching = []
    for uid, mark in sorted(shown.items()):
        item = presence.watching(mark, privacy.get(uid, privacy_rules.OPEN), titles)
        if item:
            watching.append({"user_id": uid, "title": item["title"], "url": SITE_URL + item["path"]})
    return {"users": sorted(shown)[:10_000], "watching": watching}


def report_once(reported: bool) -> bool:
    """Отправить пакет; True — в нём кто-то был. Пустой пакет («сейчас никого») уходит один раз —
    после пакета, в котором кто-то был, — дальше молчим."""
    batch = build_batch()
    if batch["users"] or reported:
        as_api.call("POST", "/clients/presence", body=batch)
    return bool(batch["users"])


_task: asyncio.Task | None = None


# Нужен ли сейчас пакет: core/functions/realtime.py отвечает «нет», пока держит соединение сервер–сервер с соцсетью
batch_needed: Callable[[], bool] = lambda: True


async def _report_loop() -> None:
    """Раз в REPORT_EVERY секунд — пакет присутствия, пока нет соединения сервер–сервер; сбой не останавливает."""
    reported = False
    while True:
        await asyncio.sleep(REPORT_EVERY)
        if not as_sessions.enabled():
            continue
        if not batch_needed():
            # Снимок при переподключении заменит всё, что соцсеть знала, — пустой пакет после него не нужен
            reported = False
            continue
        try:
            reported = await asyncio.to_thread(report_once, reported)
        except as_api.ASApiError as exc:
            log.warning("Anime Social presence batch failed: %s %s", exc.status, exc.code)
        except Exception:  # noqa: BLE001 — сбой базы не останавливает следующие пакеты
            log.exception("Could not report presence to Anime Social")


async def startup() -> None:
    """Запустить отправку пакетов."""
    global _task
    if _task is None:
        _task = asyncio.create_task(_report_loop())


async def shutdown() -> None:
    """Остановить отправку пакетов."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
