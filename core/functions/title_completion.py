"""
Досмотр тайтла для соцсети: событие completed — факт «впервые досмотрел последнюю серию», а не правило списков.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Последнюю серию определяет каталог сервера — кеш страниц тайтлов aviev_title_pages, который пишет только сервер
(core/modules/title_cache.py), а не число серий из запроса плеера: иначе один запрос «серия 1 из 1» давал бы досмотр
любого тайтла. Тайтл вышел целиком (FINISHED) — последняя серия равна числу серий; онгоинг, анонс или число неизвестно
— события нет. Сравнение строгое: у части источников нумерация сквозная (второй сезон — серии 13–24 при 12 у MAL),
и «больше или равно» засчитывало бы досмотр на первой же серии.

Где засчитывается:
- episode_done — серия впервые досмотрена (core/functions/progress.upsert_episode_progress, все три места досмотра).
  Тайтла в кеше нет — он грузится из каталога (unknown_title подключает core.py), досмотр засчитает title_finished;
- title_finished — кеш страницы узнал, что тайтл вышел целиком (новая запись или смена статуса): тем, кто в окне
  соцсети впервые досмотрел его последнюю серию и действительно смотрел её (строка просмотра ep=N), — правило списков
  (lists.auto_complete: списки — функция самого AsoPlay, их меняем всем), а тем, у кого живая сессия соцсети, ещё и
  completed. Так засчитывается и досмотр в день выхода финала, пока кеш считал тайтл онгоингом. Повтора нет: событие
  пишется один раз (as_events.record, INSERT IGNORE), уже «Просмотрено» правило не трогает. После commit announce
  будит отправителя и шлёт людям кадр «lists» (lists_moved).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable

from core.functions import as_events, lists

log = logging.getLogger("asoplay.title_completion")

FINISHED = "Finished Airing"
DONE = "p.duration > 0 AND (p.seconds >= p.duration * 0.92 OR p.seconds >= GREATEST(0, p.duration - 90))"

# Загрузить тайтл из каталога (title_cache.refresh_soon); подключает core.py до первого запроса
unknown_title: Callable[[int], None] | None = None
# Кадр «lists» людям, чьи списки поменял досмотр (lists_sweep.announce); подключает core.py, в утилитах его нет
lists_moved: Callable[[list[tuple[int, dict[str, Any]]]], None] | None = None


def last_episode(cur: Any, mal_id: int) -> int | None:
    """Последняя серия по каталогу сервера: тайтл вышел целиком — число серий; онгоинг, анонс или число неизвестно —
    0; тайтла в кеше нет — None."""
    cur.execute("SELECT episodes_total, airing_status FROM aviev_title_pages WHERE mal_id = %s", (mal_id,))
    row = cur.fetchone()
    if row is None:
        return None
    total, status = row
    return int(total) if total and str(status or "").strip() == FINISHED else 0


def episode_done(conn: Any, user_id: int, mal_id: int, episode: int, at: datetime) -> bool:
    """Серия впервые досмотрена: последняя по каталогу — событие completed (курсором действия). Тайтла в кеше нет —
    загрузить его; засчитает title_finished. True — событие записано."""
    last = last_episode(conn.cursor(), mal_id)
    if last is None:
        if unknown_title is not None:
            try:
                unknown_title(mal_id)
            except Exception:  # noqa: BLE001 — загрузка каталога не должна ломать сохранение прогресса
                log.exception("Could not ask for title %s from the catalog", mal_id)
        return False
    return bool(last) and episode == last and as_events.record(conn, user_id, "completed", mal_id, at=at)


def title_finished(conn: Any, payload: dict[str, Any], last: int,
                   now: datetime) -> tuple[int, list[tuple[int, dict[str, Any]]]]:
    """Тайтл из payload (запись каталога) стал известен как вышедший целиком: всем, кто за окно соцсети впервые
    досмотрел его последнюю серию и смотрел её (строка просмотра ep=N; отмеченные сами серии не в счёт), — правило
    списков, тем, у кого живая сессия соцсети, ещё и completed; время — записи прогресса. (событий, [(user_id, новая
    запись списка)]); commit — за вызывающим, затем announce."""
    if not last:
        return 0, []
    mal_id = int(payload["mal_id"])
    title = payload.get("title_ru") or payload.get("title_en") or payload.get("title_jp") or ""
    cur = conn.cursor()
    cur.execute(
        f"""SELECT p.user_id, p.updated_at,
                   EXISTS (SELECT 1 FROM aviev_as_sessions s WHERE s.user_id = p.user_id
                           AND s.refresh_expires_at > %s) AS linked
            FROM aviev_episode_progress p
            WHERE p.mal_id = %s AND p.episode_num = %s AND p.updated_at >= %s AND {DONE}
              AND EXISTS (SELECT 1 FROM aviev_activity a WHERE a.user_id = p.user_id AND a.mal_id = p.mal_id
                          AND a.kind IN ('watch_start', 'watch_continue') AND a.meta = %s AND a.at >= %s)""",
        (int(time.time()), mal_id, last, now - as_events.WINDOW, f"ep={last}", now - as_events.WINDOW),
    )
    events, moved = 0, []
    for user_id, at, linked in cur.fetchall():
        if linked:
            events += bool(as_events.record(conn, int(user_id), "completed", mal_id, at=at))
        entry = lists.auto_complete(conn, int(user_id), mal_id, at=at, title=title,
                                    poster_url=payload.get("poster_url") or "")
        if entry:
            moved.append((int(user_id), entry))
    return events, moved


def announce(events: int, moved: list[tuple[int, dict[str, Any]]]) -> None:
    """После commit title_finished: есть события — разбудить отправителя; поменялись списки — кадр «lists» людям."""
    if events:
        as_events.wake()
    if moved and lists_moved is not None:
        lists_moved(moved)
