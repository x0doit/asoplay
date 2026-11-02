"""
Досылка задним числом на поддельной базе: без --apply только числа, серии — только с записью просмотра, досмотр —
только из авто-правила, старше окна — не берётся, в очередь — с настоящим временем, оценка не заменяет более новую;
--since — полночь по Киеву с его переходом времени.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Пояс в проверках main — фиксированный UTC+2 (Киев в конце октября): у Python из OSPanel нет базы часовых поясов
IANA. Сам переход времени проверяется там, где база есть (сервер); без неё эта проверка пропускается с причиной.
"""
import contextlib
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.api.tools import as_events_backfill as backfill
from core.classes import clock, db
from core.config import env
from core.tests.as_events.lib import FakeConn

NOW = datetime(2026, 11, 2, 12, 0)
FRESH, OLD = datetime(2026, 10, 31, 19, 5), datetime(2026, 10, 20, 8, 0)
KYIV_LATE_OCTOBER = timezone(timedelta(hours=2))


def _has_zone(key: str) -> bool:
    try:
        ZoneInfo(key)
    except ZoneInfoNotFoundError:
        return False
    return True


def answer(sql, params):
    if "FROM aviev_episode_progress p" in sql:
        return [(20, 52991, 6, None, FRESH), (20, 52991, 7, None, OLD)], 1
    if "a.kind = 'complete'" in sql:
        return [(20, 52991, 0, None, FRESH)], 1
    if "FROM aviev_title_ratings r" in sql:
        return [(31, 1535, 0, 9, FRESH)], 1
    return [], 1


class Backfill(unittest.TestCase):
    def run_tool(self, *args):
        conn = FakeConn(answer)
        out = io.StringIO()
        with patch.object(env, "load"), patch.object(db, "connect", return_value=conn), \
                patch.object(clock, "utc_now", return_value=NOW), contextlib.redirect_stdout(out), \
                patch.object(backfill, "ZoneInfo", lambda key: KYIV_LATE_OCTOBER):
            code = backfill.main(["--since", "2026-10-30", *args])
        self.assertEqual(code, 0)
        return conn, out.getvalue()

    def test_a_dry_run_only_counts_by_type(self):
        conn, out = self.run_tool()
        self.assertIn("since 2026-10-29 22:00 UTC, window 8 days, people 2", out)  # полночь 30.10 по Киеву
        self.assertIn("  episode: 1\n  completed: 1\n  rating: 1\n  older than the window (skipped): 1", out)
        self.assertEqual(conn.statements("INSERT"), [])
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))

    def test_episodes_need_a_watch_record_and_completed_comes_only_from_the_auto_rule(self):
        conn, _ = self.run_tool()
        episodes, completed, rated = conn.sql
        self.assertIn("a.kind IN ('watch_start', 'watch_continue') AND a.meta = CONCAT('ep=', p.episode_num)",
                      episodes[0])
        self.assertIn("a.kind = 'complete'", completed[0])
        self.assertNotIn("aviev_user_lists", " ".join(sql for sql, _ in conn.sql))  # ручное «Просмотрено» — нет
        for sql, params in conn.sql:
            self.assertIn("s.refresh_expires_at > %s", sql)                       # живая сессия соцсети
            self.assertEqual(params[0], datetime(2026, 10, 29, 22, 0))

    def test_apply_queues_with_the_real_time_and_keeps_newer_ratings(self):
        conn, out = self.run_tool("--apply")
        once = conn.statements("INSERT IGNORE INTO aviev_as_events")
        self.assertEqual([p for _, p in once], [(20, "episode", 52991, 6, FRESH), (20, "completed", 52991, 0, FRESH)])
        (rating, params), = conn.statements("INSERT INTO aviev_as_events")
        self.assertIn("score = IF(VALUES(at) >= at, VALUES(score), score), at = GREATEST(at, VALUES(at))", rating)
        self.assertEqual(params, (31, 1535, 9, FRESH))
        self.assertEqual(conn.commits, 1)
        self.assertIn("queued: episode 1, completed 1, rating 1", out)

    def test_without_a_time_zone_database_it_refuses_before_touching_the_database(self):
        out = io.StringIO()
        missing = ZoneInfoNotFoundError("Europe/Kyiv")
        with patch.object(backfill, "ZoneInfo", side_effect=missing), patch.object(db, "connect") as connect, \
                contextlib.redirect_stdout(out):
            self.assertEqual(backfill.main(["--since", "2026-10-30"]), 2)
        connect.assert_not_called()
        self.assertIn("no IANA time zone Europe/Kyiv", out.getvalue())

    @unittest.skipUnless(_has_zone("Europe/Kyiv"), "нет базы часовых поясов IANA (Windows без tzdata) — на сервере")
    def test_since_is_midnight_in_kyiv_with_its_clock_change(self):
        self.assertEqual(backfill.since_utc("2026-10-30"), datetime(2026, 10, 29, 22, 0))  # зимнее время, UTC+2
        self.assertEqual(backfill.since_utc("2026-10-20"), datetime(2026, 10, 19, 21, 0))  # летнее, UTC+3
