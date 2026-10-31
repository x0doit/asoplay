"""
Списки тайтлов: статусы, избранное и правила, которые ставят статус сами.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Одна таблица `aviev_user_lists` держит и основной статус (watching / planned /
completed / dropped / postponed), и отдельный флаг is_favorite. Каждая
запись помечена `status_source` = manual | auto — manual побеждает auto.

Автоматические правила:
    - на 10-й минуте просмотра тайтл попадает в watching (auto),
    - на последней серии — в completed (auto),
    - через 30 дней простоя watching → dropped (auto).
Все три правила подчиняются настройке `auto_add_lists` в aviev_account_settings. Первые два считает раздел
/account/lists/progress-event (core/api/sections/lists), третье — sweep_dropped_after_30_days ниже; по расписанию его
запускает core/functions/lists_sweep.py.

Действия человека пишутся в активность (core.functions.activity) там, где он их делает. Автоперенос через 30 дней
в активность не пишется: график профиля — о том, что человек делал сам; в списке перенос виден по status_source='auto'.

Публичный профиль (раздел /profile/… и страница /@handle) берёт отсюда счётчики и записи человека — counts_for
и items_for; соединение с базой они открывают сами.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from core.classes.clock import utc_now
from core.classes.db import connect
from core.classes.jikan import catalog as jikan_catalog

STATUSES = ("watching", "planned", "completed", "dropped", "postponed")
SWEEP_DAYS = 30
SWEEP_BATCH = 500


async def mal_id_exists(mal_id: int) -> bool:
    """Проверяет, существует ли запись в MyAnimeList. Подтягивает Jikan,
    кэш у нас уже стоит (1 ч per-id), так что проверка практически бесплатная.

    Возвращает True при HTTP 200, False при 404, True при 5xx — потому что
    временная недоступность Jikan не повод блокировать пользователя. Это
    «сильная валидация для откровенных мертвецов, мягкая для флейковой сети».
    """
    try:
        _, _, status = await jikan_catalog.fetch(f"anime/{mal_id}", timeout=6)
    except Exception:
        return True
    if status == 404:
        return False
    return True


# ---------- helpers ----------
def row_to_public(row: tuple) -> dict[str, Any]:
    """Строка aviev_user_lists — в запись списка для ответа."""
    (user_id, mal_id, status, source, is_fav, title, poster, added_at, updated_at) = row
    return {
        "mal_id": int(mal_id),
        "status": status,
        "status_source": source,
        "is_favorite": bool(is_fav),
        "title": title or "",
        "poster_url": poster or "",
        "added_at": added_at.isoformat() if added_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def auto_add_enabled(conn, user_id: int) -> bool:
    """Включены ли у человека автоматические правила списков (по умолчанию — да)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT auto_add_lists FROM aviev_account_settings WHERE user_id=%s",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return True  # default ON
    return bool(row[0])


def fetch_entry(conn, user_id: int, mal_id: int) -> dict[str, Any] | None:
    """Запись тайтла в списках человека или None."""
    cur = conn.cursor()
    cur.execute(
        """SELECT user_id, mal_id, status, status_source, is_favorite,
                  title, poster_url, added_at, updated_at
            FROM aviev_user_lists WHERE user_id=%s AND mal_id=%s""",
        (user_id, mal_id),
    )
    row = cur.fetchone()
    return row_to_public(row) if row else None


def upsert_status(conn, user_id: int, mal_id: int, status: str | None,
                  source: str, title: str = "", poster_url: str = "") -> None:
    """Поставить статус тайтла (manual или auto); избранное не трогается."""
    now = utc_now()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO aviev_user_lists
            (user_id, mal_id, status, status_source, is_favorite,
             title, poster_url, added_at, updated_at)
            VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status=VALUES(status),
                status_source=VALUES(status_source),
                title=IF(VALUES(title)<>'', VALUES(title), title),
                poster_url=IF(VALUES(poster_url)<>'', VALUES(poster_url), poster_url),
                updated_at=VALUES(updated_at)""",
        (user_id, mal_id, status, source, title, poster_url, now, now),
    )


# ---------- правило 30 дней ----------
# Кандидат — одно условие и для выборки, и для UPDATE: правка между ними (ручной статус, новая серия, выключенное
# автодобавление) строку уже не перенесёт. Простой — и в списке, и в плеере: запись не менялась 30 дней и ни одной
# серии этого тайтла за 30 дней. Только auto-«Смотрю» и только у тех, кто не выключил автодобавление. Обход — по ключу
# (user_id, mal_id) после курсора: строка, которую UPDATE не взял, назад не вернётся, и проход кончается всегда.
_SWEEP_CANDIDATE = """l.status = 'watching' AND l.status_source = 'auto' AND l.updated_at < %s
       AND NOT EXISTS (SELECT 1 FROM aviev_episode_progress p
                        WHERE p.user_id = l.user_id AND p.mal_id = l.mal_id AND p.updated_at >= %s)
       AND COALESCE((SELECT s.auto_add_lists FROM aviev_account_settings s WHERE s.user_id = l.user_id), 1) = 1"""
_SWEEP_UPDATE = f"""UPDATE aviev_user_lists l SET l.status = 'dropped', l.updated_at = %s
     WHERE l.user_id = %s AND l.mal_id = %s AND {_SWEEP_CANDIDATE}"""


def _sweep_select(user_id: int | None, after: tuple[int, int] | None) -> str:
    """Следующая пачка кандидатов: по всей базе или у одного человека, после курсора."""
    scope = ""
    if user_id is not None:
        scope = " AND l.user_id = %s" + (" AND l.mal_id > %s" if after else "")
    elif after:
        scope = " AND (l.user_id > %s OR (l.user_id = %s AND l.mal_id > %s))"
    return f"""SELECT l.user_id, l.mal_id, l.is_favorite, l.title, l.poster_url, l.added_at
      FROM aviev_user_lists l
     WHERE {_SWEEP_CANDIDATE}{scope}
     ORDER BY l.user_id, l.mal_id
     LIMIT %s"""


def sweep_dropped_after_30_days(conn, now: datetime, *, user_id: int | None = None, batch: int = SWEEP_BATCH,
                                on_batch: Callable[[list[tuple[int, dict[str, Any]]]], None] | None = None,
                                stop: Callable[[], bool] | None = None) -> int:
    """Переводит auto-«Смотрю» после 30 дней простоя в auto-«Брошено»: по всей базе или у одного человека.

    Пачками по batch строк, каждая — своим commit; после commit — on_batch([(user_id, запись списка), …]) с тем,
    что перенесено. stop() между пачками прерывает проход. Manual-статусы не трогает. Вернёт, сколько перенесено.
    """
    now = now.replace(microsecond=0)
    cutoff = now - timedelta(days=SWEEP_DAYS)
    cur = conn.cursor()
    after: tuple[int, int] | None = None
    moved = 0
    while not (stop and stop()):
        if user_id is not None:
            scope = (user_id, after[1]) if after else (user_id,)
        else:
            scope = (after[0], after[0], after[1]) if after else ()
        cur.execute(_sweep_select(user_id, after), (cutoff, cutoff, *scope, batch))
        rows = cur.fetchall()
        if not rows:
            break
        entries = []
        for uid, mal_id, is_fav, title, poster, added_at in rows:
            cur.execute(_SWEEP_UPDATE, (now, uid, mal_id, cutoff, cutoff))
            if cur.rowcount:
                entries.append((int(uid), row_to_public(
                    (uid, mal_id, "dropped", "auto", is_fav, title, poster, added_at, now))))
        conn.commit()
        moved += len(entries)
        if entries and on_batch:
            on_batch(entries)
        if len(rows) < batch:
            break
        after = (int(rows[-1][0]), int(rows[-1][1]))
    return moved


# ---------- публичный профиль ----------
def counts_for(user_id: int) -> dict[str, int]:
    """Сколько тайтлов у человека в каждом статусе и в избранном — для публичного профиля."""
    out = {s: 0 for s in STATUSES}
    out["favorite"] = 0
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT status, COUNT(*) FROM aviev_user_lists
                WHERE user_id=%s AND status IS NOT NULL GROUP BY status""",
            (user_id,),
        )
        for status, n in cur.fetchall():
            if status in out:
                out[status] = int(n)
        cur.execute(
            "SELECT COUNT(*) FROM aviev_user_lists WHERE user_id=%s AND is_favorite=1",
            (user_id,),
        )
        out["favorite"] = int(cur.fetchone()[0] or 0)
    return out


def items_for(user_id: int, status: str | None) -> list[dict[str, Any]]:
    """Записи одного статуса или избранного (status="favorite"), до 200, свежие сверху — для публичного профиля;
    другой status — пусто."""
    with connect() as conn:
        cur = conn.cursor()
        if status == "favorite":
            cur.execute(
                """SELECT mal_id, status, status_source, is_favorite,
                          title, poster_url, updated_at
                    FROM aviev_user_lists
                    WHERE user_id=%s AND is_favorite=1
                    ORDER BY updated_at DESC LIMIT 200""",
                (user_id,),
            )
        elif status in STATUSES:
            cur.execute(
                """SELECT mal_id, status, status_source, is_favorite,
                          title, poster_url, updated_at
                    FROM aviev_user_lists
                    WHERE user_id=%s AND status=%s
                    ORDER BY updated_at DESC LIMIT 200""",
                (user_id, status),
            )
        else:
            return []
        rows = cur.fetchall()
    return [
        {
            "mal_id": int(r[0]),
            "status": r[1],
            "status_source": r[2],
            "is_favorite": bool(r[3]),
            "title": r[4] or "",
            "poster_url": r[5] or "",
            "updated_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]
