"""
Журнал активности: запись событий человека (watch_start, watch_continue, list_add, list_move, list_remove, favorite,
unfavorite, rate, complete) в aviev_activity — по ним строится contribution-график профиля.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

record_event пишет синхронно в текущем соединении; commit — за вызывающим кодом. Чтение ленты и графика — в
core/functions/activity_feed.py, маршруты /account/activity — в core/api/sections/activity.py.
"""
from __future__ import annotations

import logging

from core.classes.clock import utc_now

log = logging.getLogger("asoplay.activity")


ALLOWED_KINDS = {
    "watch_start", "watch_continue", "list_add", "list_move", "list_remove",
    "favorite", "unfavorite", "rate", "complete",
}

# Группировка kind'ов для UI-фильтра активности. «Все» = без фильтра.
KIND_GROUPS = {
    "watch":     ["watch_start", "watch_continue", "complete"],
    "lists":     ["list_add", "list_move", "list_remove"],
    "favorites": ["favorite", "unfavorite"],
    "rate":      ["rate"],
}

# Для этих событий дедуп должен быть «один раз в день на тайтл», а не по
# конкретному значению meta. Иначе 5 переносов «watching→dropped→watching→…»
# спамят активность. Пишем их с пустой meta — уникальный индекс
# (user, day, kind, mal_id, meta(64)) схлопнёт дубликаты на уровне INSERT IGNORE.
DEDUP_BY_TITLE_PER_DAY = {
    "list_add", "list_move", "list_remove",
    "favorite", "unfavorite", "complete", "rate",
}


def record_event(conn, user_id: int, *, kind: str, mal_id: int | None = None,
                 meta: str | None = None) -> None:
    """Пишет событие в aviev_activity.

    Для watch_* дедуп по (user, day, kind, mal_id, ep) — разные серии
    считаются отдельно, но пауза/перемотка на одной серии в тот же день — нет.

    Для list_* / favorite / complete / rate дедуп просто по (user, day, kind,
    mal_id) — активность не спамится при перекидывании тайтла между списками.
    """
    if kind not in ALLOWED_KINDS:
        log.debug("record_event: unknown kind=%s", kind)
        return
    stored_meta = "" if kind in DEDUP_BY_TITLE_PER_DAY else (meta or "")
    now = utc_now()
    cur = conn.cursor()
    cur.execute(
        """INSERT IGNORE INTO aviev_activity
            (user_id, day, kind, mal_id, meta, at)
            VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, now.date(), kind, mal_id, stored_meta[:255], now),
    )
