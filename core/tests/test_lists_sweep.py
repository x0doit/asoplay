"""
Правило 30 дней без базы: проход пачками по курсору, условие простоя, расписание, замок и отметка прохода, кадры
вкладкам, ручной прогон.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

С настоящей базой и двумя процессами сразу правило проверяет test_lists_sweep_db.py.
"""
import asyncio
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.api.sections import lists as lists_section
from core.api.sections.lists import progress_event
from core.classes import db
from core.functions import lists, lists_sweep, realtime

NOW = datetime(2026, 10, 31, 4, 30, 15, 123456)
SECONDS = NOW.replace(microsecond=0)
CUTOFF = SECONDS - timedelta(days=30)
CANDIDATE = ("l.status = 'watching' AND l.status_source = 'auto' AND l.updated_at < %s AND NOT EXISTS (SELECT 1 FROM "
             "aviev_episode_progress p WHERE p.user_id = l.user_id AND p.mal_id = l.mal_id AND p.updated_at >= %s) "
             "AND COALESCE((SELECT s.auto_add_lists FROM aviev_account_settings s WHERE s.user_id = l.user_id), 1) = 1")


class SeqCursor:
    """Отвечает по ключу в тексте запроса — ответами по очереди, потом пусто; UPDATE списка — rowcount по очереди."""

    def __init__(self, answers=None, rowcounts=()):
        self.answers = {key: list(rows) for key, rows in (answers or {}).items()}
        self.rowcounts = list(rowcounts)
        self.queries, self.rows, self.rowcount = [], [], 0

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.queries.append((flat, tuple(params)))
        key = next((k for k in self.answers if k in flat), None)
        self.rows = self.answers[key].pop(0) if key and self.answers[key] else []
        if flat.startswith("UPDATE aviev_user_lists"):
            self.rowcount = self.rowcounts.pop(0) if self.rowcounts else 0

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def sql(self, start):
        return [(q, p) for q, p in self.queries if q.startswith(start)]


class FakeConn:
    def __init__(self, cursor):
        self._cursor, self.commits = cursor, 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def row(uid, mal_id):
    return (uid, mal_id, 0, f"Тайтл {mal_id}", "", datetime(2026, 9, 1, 12, 0))


class SweepTests(unittest.TestCase):
    def sweep(self, batches, rowcounts, **kw):
        cur = SeqCursor({"FROM aviev_user_lists l": batches}, rowcounts)
        conn, calls = FakeConn(cur), []
        moved = lists.sweep_dropped_after_30_days(conn, NOW, on_batch=calls.append, **kw)
        return moved, cur, conn, calls

    def test_batches_follow_the_key_cursor_and_are_committed_one_by_one(self):
        moved, cur, conn, calls = self.sweep([[row(5, 1), row(5, 2)], [row(7, 3)]], [1, 0, 1], batch=2)
        first, second = cur.sql("SELECT l.user_id")
        self.assertNotIn("l.user_id >", first[0])                                  # первая пачка — с начала
        self.assertIn("AND (l.user_id > %s OR (l.user_id = %s AND l.mal_id > %s)) ORDER BY l.user_id, l.mal_id",
                      second[0])
        self.assertEqual((first[1], second[1]), ((CUTOFF, CUTOFF, 2), (CUTOFF, CUTOFF, 5, 5, 2, 2)))
        self.assertEqual((moved, conn.commits), (2, 2))
        self.assertEqual([[(u, e["mal_id"], e["status"], e["status_source"]) for u, e in c] for c in calls],
                         [[(5, 1, "dropped", "auto")], [(7, 3, "dropped", "auto")]])
        self.assertEqual(calls[0][0][1]["updated_at"], SECONDS.isoformat())

    def test_rows_the_update_did_not_take_do_not_come_back(self):
        # Полные пачки, где ничего не перенеслось (ручная правка между SELECT и UPDATE): проход идёт дальше и кончается
        moved, cur, _, calls = self.sweep([[row(5, 1), row(5, 2)], [row(5, 3), row(6, 1)], []], [0, 0, 0, 0], batch=2)
        self.assertEqual((moved, calls), (0, []))
        self.assertEqual([p[2:5] for _, p in cur.sql("SELECT l.user_id")[1:]], [(5, 5, 2), (6, 6, 1)])

    def test_select_and_update_check_the_same_candidate_condition(self):
        # Простой в списке и в плеере, автодобавление не выключено — в выборке и в UPDATE одно и то же условие:
        # правка между ними (ручной статус, новая серия, выключенное автодобавление) строку не перенесёт
        _, cur, _, _ = self.sweep([[row(5, 1)]], [1])
        select, update = cur.sql("SELECT l.user_id")[0][0], cur.sql("UPDATE aviev_user_lists")[0]
        self.assertIn(f"WHERE {CANDIDATE} ORDER BY", select)
        self.assertTrue(update[0].endswith(f"AND {CANDIDATE}"), update[0])
        self.assertEqual(update[1], (SECONDS, 5, 1, CUTOFF, CUTOFF))

    def test_the_activity_graph_gets_nothing(self):
        _, cur, _, _ = self.sweep([[row(5, 1)]], [1])
        self.assertEqual([q for q, _ in cur.queries if "aviev_activity" in q], [])

    def test_one_person_only(self):
        _, cur, _, _ = self.sweep([[row(5, 1), row(5, 2)], []], [1, 1], user_id=5, batch=2)
        first, second = cur.sql("SELECT l.user_id")
        self.assertIn("AND l.user_id = %s ORDER BY", first[0])
        self.assertIn("AND l.user_id = %s AND l.mal_id > %s ORDER BY", second[0])
        self.assertEqual((first[1][2:], second[1][2:]), ((5, 2), (5, 2, 2)))

    def test_stop_ends_the_pass_between_batches(self):
        stop = iter([False, True])
        moved, cur, _, _ = self.sweep([[row(5, 1), row(5, 2)], [row(6, 1)]], [1, 1, 1], batch=2,
                                      stop=lambda: next(stop))
        self.assertEqual((moved, len(cur.sql("SELECT l.user_id"))), (2, 1))


class ScheduleTests(unittest.TestCase):
    def test_the_slot_is_the_last_planned_time(self):
        at = (4, 0)
        self.assertEqual(lists_sweep.last_slot(datetime(2026, 10, 31, 4, 0), at), datetime(2026, 10, 31, 4, 0))
        self.assertEqual(lists_sweep.last_slot(datetime(2026, 10, 31, 3, 59), at), datetime(2026, 10, 30, 4, 0))
        self.assertEqual(lists_sweep.seconds_to_next(datetime(2026, 10, 31, 3, 0), at), 3600)
        self.assertEqual(lists_sweep.seconds_to_next(datetime(2026, 10, 31, 4, 0), at), 86400)

    def test_the_time_comes_from_the_setting(self):
        for raw, want in (("06:30", (6, 30)), ("00:00", (0, 0)), ("", (4, 0)), ("off", None), ("OFF", None)):
            with patch.dict(os.environ, {"AV_LISTS_SWEEP_AT": raw}):
                self.assertEqual(lists_sweep.sweep_at(), want, raw)

    def test_a_wrong_time_goes_to_the_log_and_falls_back_to_four(self):
        for raw in ("25:00", "6:30", "4", "утром"):
            with patch.dict(os.environ, {"AV_LISTS_SWEEP_AT": raw}), self.assertLogs("asoplay.lists_sweep", "ERROR"):
                self.assertEqual(lists_sweep.sweep_at(), (4, 0))


class RunOnceTests(unittest.TestCase):
    def run_once(self, answers):
        cur = SeqCursor(answers)
        conn = FakeConn(cur)
        with patch.object(lists_sweep, "connect", return_value=conn), \
                patch.object(lists, "sweep_dropped_after_30_days", return_value=3) as called:
            result = lists_sweep.run_once(NOW)
        return result, cur, conn, called

    def test_a_due_pass_is_marked_started_and_finished_under_the_lock(self):
        result, cur, conn, called = self.run_once({"GET_LOCK": [[(1,)]], "FROM aviev_task_runs": [
            [(datetime(2026, 10, 30, 4, 0), datetime(2026, 10, 30, 4, 1))]]})
        self.assertEqual(result, 3)
        self.assertEqual(cur.queries[0], ("SELECT GET_LOCK(%s, 0)", ("asoplay:lists_sweep",)))
        self.assertEqual(cur.sql("INSERT INTO aviev_task_runs")[0][1], ("lists_sweep", NOW))
        finished = cur.sql("UPDATE aviev_task_runs")[0][1]
        self.assertEqual((finished[1:], conn.commits), ((3, "lists_sweep"), 2))
        self.assertEqual(cur.queries[-1], ("SELECT RELEASE_LOCK(%s)", ("asoplay:lists_sweep",)))
        self.assertEqual((called.call_args.args[1], called.call_args.kwargs["batch"]), (NOW, lists.SWEEP_BATCH))

    def test_the_first_pass_ever_runs_at_once(self):
        self.assertEqual(self.run_once({"GET_LOCK": [[(1,)]]})[0], 3)

    def test_a_pass_done_after_the_planned_time_is_not_repeated(self):
        result, cur, _, called = self.run_once({"GET_LOCK": [[(1,)]], "FROM aviev_task_runs": [
            [(datetime(2026, 10, 31, 4, 0, 5), datetime(2026, 10, 31, 4, 2))]]})
        self.assertIsNone(result)
        called.assert_not_called()
        self.assertEqual(cur.queries[-1][0], "SELECT RELEASE_LOCK(%s)")

    def test_a_pass_that_broke_off_is_run_again(self):
        result, _, _, called = self.run_once({"GET_LOCK": [[(1,)]], "FROM aviev_task_runs": [
            [(datetime(2026, 10, 31, 4, 0, 5), None)]]})
        self.assertEqual(result, 3)
        called.assert_called_once()

    def test_a_busy_lock_means_another_process_is_on_it(self):
        with self.assertLogs("asoplay.lists_sweep", "INFO"):
            result, cur, _, called = self.run_once({"GET_LOCK": [[(0,)]]})
        self.assertIsNone(result)
        called.assert_not_called()
        self.assertEqual(len(cur.queries), 1)                       # ни отметки, ни RELEASE: замок не наш

    def test_a_failed_pass_releases_the_lock_and_stays_unfinished(self):
        cur = SeqCursor({"GET_LOCK": [[(1,)]]})
        with patch.object(lists_sweep, "connect", return_value=FakeConn(cur)), \
                patch.object(lists, "sweep_dropped_after_30_days", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                lists_sweep.run_once(NOW)
        self.assertEqual(cur.queries[-1][0], "SELECT RELEASE_LOCK(%s)")
        self.assertEqual(cur.sql("UPDATE aviev_task_runs"), [])     # конец не отмечен — проход повторят


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_pass_goes_to_the_log_and_is_retried_soon(self):
        sleeps, results = [], iter([RuntimeError("db down"), 0])

        async def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise asyncio.CancelledError

        def run(**kw):
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result
        with patch.object(lists_sweep, "run_once", side_effect=run), \
                patch.object(lists_sweep, "utc_now", return_value=datetime(2026, 10, 31, 10, 0)), \
                self.assertLogs("asoplay.lists_sweep", "ERROR"), self.assertRaises(asyncio.CancelledError):
            await lists_sweep._loop((4, 0), sleep=sleep)
        self.assertEqual(sleeps, [lists_sweep.RETRY_AFTER, 18 * 3600])

    async def test_startup_starts_and_shutdown_stops_the_schedule(self):
        self.addCleanup(lists_sweep._stop.clear)          # остановка — на весь процесс; другим проверкам она не нужна
        with patch.dict(os.environ, {"AV_LISTS_SWEEP_AT": "04:00"}), \
                patch.object(lists_sweep, "_loop", side_effect=lambda at: asyncio.sleep(3600)):
            await lists_sweep.startup()
            task = lists_sweep._task
            await lists_sweep.shutdown()
        await asyncio.sleep(0)
        self.assertTrue(task.cancelled())
        self.assertTrue(lists_sweep._stop.is_set())
        self.assertIsNone(lists_sweep._task)

    async def test_off_starts_nothing(self):
        with patch.dict(os.environ, {"AV_LISTS_SWEEP_AT": "off"}), self.assertLogs("asoplay.lists_sweep", "INFO"):
            await lists_sweep.startup()
        self.assertIsNone(lists_sweep._task)


class AnnounceTests(unittest.TestCase):
    def test_each_person_gets_their_own_records_in_one_frame(self):
        sent = []
        with patch.object(realtime, "lists_changed", side_effect=lambda uid, items: sent.append(
                (uid, [i["mal_id"] for i in items]))):
            lists_sweep.announce([(5, {"mal_id": 1}), (7, {"mal_id": 3}), (5, {"mal_id": 2})])
        self.assertEqual(sent, [(5, [1, 2]), (7, [3])])


class ManualSweepTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(lists_section.router)
        app.dependency_overrides[db.current_user_required] = lambda: {"id": 5}
        self.client = TestClient(app)

    def call(self, auto_add, moved=2):
        conn = FakeConn(SeqCursor({"SELECT auto_add_lists": [[(auto_add,)]]}))
        with patch.object(progress_event, "connect", return_value=conn), \
                patch.object(progress_event, "sweep_dropped_after_30_days", return_value=moved) as sweep, \
                patch.object(progress_event, "utc_now", return_value=NOW):
            return self.client.post("/account/lists/sweep").json(), sweep

    def test_the_manual_run_is_the_same_pass_for_one_person(self):
        answer, sweep = self.call(1)
        self.assertEqual(answer, {"applied": 2})
        self.assertEqual((sweep.call_args.args[1], sweep.call_args.kwargs["user_id"]), (NOW, 5))
        self.assertIs(sweep.call_args.kwargs["on_batch"], lists_sweep.announce)

    def test_switched_off_auto_rules_are_not_run(self):
        answer, sweep = self.call(0)
        self.assertEqual(answer, {"applied": 0, "note": "auto_add_lists disabled"})
        sweep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
