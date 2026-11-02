"""
Досылка событий просмотра соцсети задним числом: из уже сохранённого — в очередь aviev_as_events с настоящим at.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Решение владельца — засчитать прошлые действия задним числом; без --apply команда только считает. Что берётся
(с полуночи --since по Киеву — по нему соцсеть считает дни и недели заданий; только люди с живой сессией соцсети
у AsoPlay — остальных соцсеть не примет):
- оценки — aviev_title_ratings, at — когда поставлена последняя оценка;
- досмотр тайтла — aviev_activity kind='complete' (это пишет только авто-правило последней серии), at — первый раз;
- серии — досмотренные в aviev_episode_progress, если по серии есть watch_start или watch_continue с ep=N
  в aviev_activity: так серии, отмеченные досмотренными сами, не уходят. at — время последней такой строки:
  updated_at прошлых серий перезаписывается, когда досмотрена следующая.
Старше окна соцсети (as_events.WINDOW) — не берутся: их уже не засчитают. В очередь — как живые события: серия и
досмотр один раз, оценка не заменяет более новую. Отправляет работающий сервер (as_events_sender, раз в минуту).

    python -m core.api.tools.as_events_backfill --since 2026-10-30            числа по типам, без записи
    python -m core.api.tools.as_events_backfill --since 2026-10-30 --apply    записать в очередь
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import env

_SESSION = "EXISTS (SELECT 1 FROM aviev_as_sessions s WHERE s.user_id = {t}.user_id AND s.refresh_expires_at > %s)"
QUERIES = {
    "episode": f"""SELECT p.user_id, p.mal_id, p.episode_num, NULL, MAX(a.at)
        FROM aviev_episode_progress p
        JOIN aviev_activity a ON a.user_id = p.user_id AND a.mal_id = p.mal_id
             AND a.kind IN ('watch_start', 'watch_continue') AND a.meta = CONCAT('ep=', p.episode_num) AND a.at >= %s
        WHERE p.duration > 0 AND (p.seconds >= p.duration * 0.92 OR p.seconds >= GREATEST(0, p.duration - 90))
          AND {_SESSION.format(t='p')}
        GROUP BY p.user_id, p.mal_id, p.episode_num""",
    "completed": f"""SELECT a.user_id, a.mal_id, 0, NULL, MIN(a.at) FROM aviev_activity a
        WHERE a.kind = 'complete' AND a.at >= %s AND {_SESSION.format(t='a')}
        GROUP BY a.user_id, a.mal_id""",
    "rating": f"""SELECT r.user_id, r.mal_id, 0, r.score, r.set_at FROM aviev_title_ratings r
        WHERE r.set_at >= %s AND {_SESSION.format(t='r')}""",
}
_ONCE = "INSERT IGNORE INTO aviev_as_events (user_id, type, mal_id, episode, at) VALUES (%s, %s, %s, %s, %s)"
_RATING = """INSERT INTO aviev_as_events (user_id, type, mal_id, episode, score, at)
    VALUES (%s, 'rating', %s, 0, %s, %s)
    ON DUPLICATE KEY UPDATE score = IF(VALUES(at) >= at, VALUES(score), score), at = GREATEST(at, VALUES(at))"""
ZONE = "Europe/Kyiv"  # часы заданий соцсети: дни и недели — по Киеву, с переходом времени


def since_utc(day: str) -> datetime:
    """Полночь дня по Киеву — в UTC без пояса, как время в таблицах. Сдвиг — из базы часовых поясов IANA:
    у Киева есть летнее время."""
    midnight = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ZoneInfo(ZONE))
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def found(cur, since: datetime, now: datetime, window: timedelta) -> dict[str, list[tuple]]:
    """Что досылать, по типам: (user_id, mal_id, episode, score, at); старше окна соцсети — отдельно, под «old»."""
    out: dict[str, list[tuple]] = {"old": []}
    for kind, sql in QUERIES.items():
        cur.execute(sql, (since, int(time.time())))
        rows = [(int(r[0]), int(r[1]), int(r[2] or 0), None if r[3] is None else int(r[3]), r[4])
                for r in cur.fetchall() if r[4] is not None]
        out[kind] = [r for r in rows if r[4] >= now - window]
        out["old"] += [(kind, *r) for r in rows if r[4] < now - window]
    return out


def queue(cur, rows: dict[str, list[tuple]]) -> dict[str, int]:
    """Записать найденное в очередь; сколько событий добавлено или обновлено по типам (уже лежащие — не в счёт)."""
    done: dict[str, int] = {}
    for kind in ("episode", "completed"):
        done[kind] = sum(bool(cur.execute(_ONCE, (u, kind, m, e, at))) for u, m, e, _, at in rows[kind])
    done["rating"] = sum(bool(cur.execute(_RATING, (u, m, s, at))) for u, m, _, s, at in rows["rating"])
    return done


def main(argv: list[str] | None = None) -> int:
    """Посчитать (и с --apply — записать) досылку; печатает только числа, без людей и тайтлов."""
    parser = argparse.ArgumentParser(prog="python -m core.api.tools.as_events_backfill")
    parser.add_argument("--since", required=True, help="день по Киеву, ГГГГ-ММ-ДД: с его полуночи")
    parser.add_argument("--apply", action="store_true", help="записать в очередь (без него — только числа)")
    args = parser.parse_args(argv)
    try:
        since = since_utc(args.since)
    except ZoneInfoNotFoundError:
        print(f"no IANA time zone {ZONE} here (Windows needs the tzdata package); run this on the server")
        return 2
    env.load()
    from core.classes.clock import utc_now
    from core.classes.db import connect
    from core.functions import as_events

    now = utc_now()
    with connect() as conn:
        cur = conn.cursor()
        rows = found(cur, since, now, as_events.WINDOW)
        people = {r[0] for kind in as_events.TYPES for r in rows[kind]}
        print(f"since {since:%Y-%m-%d %H:%M} UTC, window {as_events.WINDOW.days} days, people {len(people)}")
        for kind in as_events.TYPES:
            print(f"  {kind}: {len(rows[kind])}")
        print(f"  older than the window (skipped): {len(rows['old'])}")
        if not args.apply:
            print("dry run: nothing written (add --apply)")
            conn.rollback()
            return 0
        done = queue(cur, rows)
        conn.commit()
    print("queued: " + ", ".join(f"{kind} {done[kind]}" for kind in as_events.TYPES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
