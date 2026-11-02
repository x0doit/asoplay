"""
Досмотр тайтла после записи страницы и правила статусов: кеш узнал, что тайтл вышел целиком, — completed тем, кто
в окне соцсети досмотрел и смотрел его последнюю серию, один раз; «в планах» уступает «Смотрю», последняя серия по
каталогу — «Просмотрено» (авто), ручные «Просмотрено», «Брошено», «Отложено» запирают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from core.api.sections.lists import progress_event
from core.functions import as_events, title_completion
from core.modules import title_cache
from core.tests.as_events.lib import AT, FakeConn

NOW = datetime(2026, 11, 2, 12, 0)
FINISHED = {"mal_id": 52991, "title_en": "Frieren", "airing_status": "Finished Airing", "episodes_total": 28}


class TitleFinished(unittest.TestCase):
    def test_finishers_of_the_last_episode_get_completed_with_their_progress_time(self):
        conn = FakeConn(lambda sql, params: ([(20, AT), (31, NOW)] if "FROM aviev_episode_progress p" in sql else [], 1))
        with patch.object(as_events, "record", return_value=True) as record:
            self.assertEqual(title_completion.title_finished(conn, 52991, 28, NOW), 2)
        (sql, params), = conn.sql
        self.assertIn("p.episode_num = %s", sql)
        self.assertIn("a.meta = %s", sql)                       # строка просмотра этой серии — не отмеченная сама
        self.assertEqual(params[:2], (52991, 28))
        self.assertEqual(params[3], "ep=28")
        self.assertEqual([(c.args[1:4], c.kwargs) for c in record.call_args_list],
                         [((20, "completed", 52991), {"at": AT}), ((31, "completed", 52991), {"at": NOW})])

    def test_no_count_no_check(self):
        conn = FakeConn()
        self.assertEqual(title_completion.title_finished(conn, 52991, 0, NOW), 0)
        self.assertEqual(conn.sql, [])


class StoringAPage(unittest.TestCase):
    def store(self, before, payload=FINISHED, sweep=True):
        def answer(sql, params):
            if sql.startswith("SELECT episodes_total, airing_status FROM aviev_title_pages"):
                return ([before] if before else []), 1
            return [], 1
        with patch.object(title_cache, "connect", return_value=FakeConn(answer)), \
                patch.object(title_completion, "title_finished", return_value=1) as finished, \
                patch.object(as_events, "wake") as wake:
            title_cache.store_title(dict(payload), sweep=sweep)
        return finished, wake

    def test_a_title_first_known_as_finished_is_checked_and_the_sender_woken(self):
        finished, wake = self.store(None)
        self.assertEqual(finished.call_args.args[1:3], (52991, 28))
        wake.assert_called_once()

    def test_a_status_or_count_change_is_checked_again_an_unchanged_page_is_not(self):
        self.assertTrue(self.store((28, "Currently Airing"))[0].called)
        self.assertTrue(self.store((24, "Finished Airing"))[0].called)
        self.assertFalse(self.store((28, "Finished Airing"))[0].called)

    def test_an_airing_title_or_a_count_only_run_is_not_checked(self):
        self.assertFalse(self.store(None, dict(FINISHED, airing_status="Currently Airing"))[0].called)
        self.assertFalse(self.store(None, sweep=False)[0].called)


class ListRules(unittest.TestCase):
    def run_event(self, entries, last=(28, "Finished Airing"), episode=28):
        statuses, kinds = [], []

        def answer(sql, params):
            if sql.startswith("SELECT episodes_total, airing_status FROM aviev_title_pages"):
                return ([last] if last else []), 1
            return [], 1
        with patch.object(progress_event, "connect", return_value=FakeConn(answer)), \
                patch.object(progress_event, "upsert_episode_progress"), \
                patch.object(progress_event, "fetch_entry", side_effect=entries), \
                patch.object(progress_event, "auto_add_enabled", return_value=True), \
                patch.object(progress_event, "upsert_status", side_effect=lambda *a, **k: statuses.append(k["status"])), \
                patch.object(progress_event, "record_event", side_effect=lambda *a, **k: kinds.append(k["kind"])), \
                patch.object(progress_event, "watched_seconds", return_value=0), patch.object(as_events, "wake"):
            event = progress_event.ProgressEventIn(mal_id=52991, episode_num=episode, seconds=1400, duration=1420,
                                                   episodes_total=99)  # число серий от плеера не используется
            progress_event.progress_event(event, {"id": 20})
        return statuses, kinds

    def test_planned_gives_way_to_watching_and_then_to_completed(self):
        planned = {"status": "planned", "status_source": "manual"}
        watching = {"status": "watching", "status_source": "auto"}
        statuses, kinds = self.run_event([planned, watching, watching])
        self.assertEqual(statuses, ["watching", "completed"])
        self.assertEqual(kinds[:2], ["list_move", "complete"])  # из планов — перенос, а не добавление

    def test_manual_completed_dropped_and_postponed_stay(self):
        for status in ("completed", "dropped", "postponed"):
            entry = {"status": status, "status_source": "manual"}
            self.assertEqual(self.run_event([entry, entry, entry])[0], [], status)

    def test_without_the_catalog_or_on_a_middle_episode_the_status_stays_watching(self):
        watching = {"status": "watching", "status_source": "auto"}
        self.assertEqual(self.run_event([watching, watching, watching], last=None)[0], [])
        self.assertEqual(self.run_event([watching, watching, watching], episode=27)[0], [])


if __name__ == "__main__":
    unittest.main()
