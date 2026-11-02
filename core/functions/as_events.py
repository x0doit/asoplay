"""
События просмотра для соцсети (socialnetwork.local/docs/api/clients.md, раздел 14): очередь aviev_as_events.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Серия впервые досмотрена, тайтл досмотрен по последней серии, поставлена оценка — по этим событиям соцсеть
засчитывает задания компендиума и достижения. Событие пишется в очередь той же транзакцией, что и само действие
(record): не сохранилось действие — нет и события. Лежит в очереди, пока соцсеть его не подтвердила; отправляет
core/functions/as_events_sender.py по соединению сервер–сервер, а обработчик после commit будит его (wake).
Пишем только тем, у кого есть живая сессия соцсети у AsoPlay: остальных соцсеть всё равно не примет.

Строка — одно событие, (user_id, type, mal_id, episode) уникальны. Серия и досмотр пишутся один раз; новая оценка
заменяет неотправленную, а после подтверждения строка удаляется, только если оценка и время те же, что ушли
(drop_sent). Старше WINDOW соцсеть событие не засчитает — такие строки удаляются с записью в журнал (take).
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, NamedTuple

from core.classes import as_sessions
from core.classes.clock import utc_now
from core.classes.db import connect

log = logging.getLogger("asoplay.as_events")

TYPES = ("episode", "completed", "rating")
BATCH = 500                    # событий в пакете — предел соцсети
WINDOW = timedelta(days=8)     # старше соцсеть не засчитывает

_ROW = "SELECT id, user_id, type, mal_id, episode, score, at FROM aviev_as_events"
_wakers: list[Callable[[], None]] = []


class Row(NamedTuple):
    """Событие в очереди."""

    id: int
    user_id: int
    type: str
    mal_id: int
    episode: int
    score: int | None
    at: datetime


def object_key(mal_id: int) -> str:
    """Ключ тайтла у соцсети — как у комментариев к странице тайтла."""
    return f"asoplay:anime:{int(mal_id)}"


def unix(at: datetime) -> int:
    """Время из базы (UTC без пояса) — в Unix-секунды строго как UTC: местный пояс сервера сдвинул бы день и неделю
    заданий компендиума."""
    return calendar.timegm(at.timetuple())


def record(conn: Any, user_id: int, kind: str, mal_id: int, *, episode: int = 0, score: int | None = None,
           at: datetime | None = None) -> bool:
    """Событие — в очередь, курсором соединения действия (commit — за вызывающим). Ошибка базы не глотается:
    действие без своего события не сохраняется. True — записано."""
    if kind not in TYPES or not as_sessions.enabled():
        return False
    cur = conn.cursor()
    if not as_sessions.linked(cur, user_id):
        return False
    stamp = at or utc_now()
    if kind == "rating":
        cur.execute(
            """INSERT INTO aviev_as_events (user_id, type, mal_id, episode, score, at)
                VALUES (%s, 'rating', %s, 0, %s, %s)
                ON DUPLICATE KEY UPDATE score = VALUES(score), at = VALUES(at)""",
            (user_id, mal_id, score, stamp),
        )
    else:
        cur.execute(
            "INSERT IGNORE INTO aviev_as_events (user_id, type, mal_id, episode, at) VALUES (%s, %s, %s, %s, %s)",
            (user_id, kind, mal_id, episode if kind == "episode" else 0, stamp),
        )
    return True


def frame(row: Row) -> dict[str, Any]:
    """Событие для кадра events: object — ключ тайтла, at — Unix-секунды; серия — у episode, оценка — у rating."""
    event: dict[str, Any] = {"user_id": row.user_id, "type": row.type, "object": object_key(row.mal_id),
                             "at": unix(row.at)}
    if row.type == "episode":
        event["episode"] = row.episode
    elif row.type == "rating":
        event["score"] = row.score
    return event


def _row(r: tuple) -> Row:
    """Строка базы — в Row."""
    return Row(int(r[0]), int(r[1]), str(r[2]), int(r[3]), int(r[4]), None if r[5] is None else int(r[5]), r[6])


def take(limit: int = BATCH) -> list[Row]:
    """Старейшие события очереди, не больше limit. Старше WINDOW — удаляются: соцсеть их уже не засчитает."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM aviev_as_events WHERE at < %s", (utc_now() - WINDOW,))
        if cur.rowcount:
            log.warning("Dropped %s Anime Social events older than %s days", cur.rowcount, WINDOW.days)
        cur.execute(_ROW + " ORDER BY id LIMIT %s", (limit,))
        rows = [_row(r) for r in cur.fetchall()]
        conn.commit()
    return rows


def drop_sent(rows: list[Row]) -> int:
    """Подтверждённые события — из очереди. Оценку, которую после отправки заменила новая, оставляем: уйдёт
    следующим пакетом. Возвращает, сколько строк удалено."""
    once = [row.id for row in rows if row.type != "rating"]
    ratings = [row for row in rows if row.type == "rating"]
    dropped = 0
    with connect() as conn:
        cur = conn.cursor()
        if once:
            marks = ", ".join(["%s"] * len(once))
            cur.execute(f"DELETE FROM aviev_as_events WHERE type <> 'rating' AND id IN ({marks})", once)
            dropped += cur.rowcount
        for row in ratings:
            cur.execute("DELETE FROM aviev_as_events WHERE id = %s AND score = %s AND at = %s",
                        (row.id, row.score, row.at))
            dropped += cur.rowcount
        conn.commit()
    return dropped


def on_new(waker: Callable[[], None]) -> None:
    """Кого будить, когда в очереди новое событие (отправитель при старте)."""
    _wakers.append(waker)


def off_new(waker: Callable[[], None]) -> None:
    """Больше не будить (отправитель остановлен)."""
    if waker in _wakers:
        _wakers.remove(waker)


def wake() -> None:
    """Из обработчика после commit: в очереди новое событие. Никогда не бросает."""
    for waker in list(_wakers):
        try:
            waker()
        except Exception:  # noqa: BLE001 — пробуждение не должно ломать ответ на запрос
            log.debug("Could not wake the Anime Social events sender", exc_info=True)
