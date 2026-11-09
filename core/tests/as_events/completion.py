"""
Досмотр тайтла после записи страницы и правила статусов: кеш узнал, что тайтл вышел целиком, — всем, кто в окне
соцсети досмотрел и смотрел его последнюю серию, правило списков со временем досмотра, а у кого живая сессия
соцсети — ещё и completed, один раз; после commit — отправитель и кадр «lists». «в планах» уступает «Смотрю»,
последняя серия по каталогу — «Просмотрено» (авто); ручная очистка, ручные «Просмотрено», «Брошено», «Отложено»
запирают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from core.api.sections.lists import progress_event
from core.functions import as_events, lists, title_completion
from core.modules import title_cache
from core.tests.as_events.lib import AT, FakeConn

NOW = datetime(2026, 11, 2, 12, 0)
FINISHED = {"mal_id": 52991, "title_ru": "Фрирен", "title_en": "Frieren", "poster_url": "https://cdn.example/f.jpg",
            "airing_status": "Finished Airing", "episodes_total": 28}
ENTRY = {"mal_id": 52991, "status": "completed", "status_source": "auto"}


class TitleFinished(unittest.TestCase):
    def check(self, rows, moved=ENTRY):
        """title_finished, где досмотревшие — rows (user_id, updated_at, linked)."""
        conn = FakeConn(lambda sql, params: (rows if "FROM aviev_episode_progress p" in sql else [], 1))
        with patch.object(as_events, "record", return_value=True) as record, \
                patch.object(lists, "auto_complete", return_value=moved) as complete:
            found = title_completion.title_finished(conn, FINISHED, 28, NOW)
        return conn, found, record, complete

    def test_everyone_gets_the_list_rule_and_linked_people_also_get_completed(self):
        conn, found, record, complete = self.check([(20, AT, 1), (31, NOW, 0)])
        (sql, params), = conn.sql
        self.assertIn("p.episode_num = %s", sql)
        self.assertIn("a.meta = %s", sql)  # строка просмотра этой серии — не отмеченная сама
        self.assertIn("AS linked", sql)    # живая сессия соцсети — колонка, а не условие
        self.assertEqual((params[1:3], params[4]), ((52991, 28), "ep=28"))
        self.assertEqual([(c.args[1:4], c.kwargs) for c in record.call_args_list],
                         [((20, "completed", 52991), {"at": AT})])
        shown = {"title": "Фрирен", "poster_url": "https://cdn.example/f.jpg"}
        self.assertEqual([(c.args[1:3], c.kwargs) for c in complete.call_args_list],
                         [((20, 52991), {"at": AT, **shown}), ((31, 52991), {"at": NOW, **shown})])
        self.assertEqual(found, (1, [(20, ENTRY), (31, ENTRY)]))

    def test_unchanged_lists_are_not_reported(self):
        self.assertEqual(self.check([(20, AT, 1)], moved=None)[1], (1, []))

    def test_no_count_no_check(self):
        conn = FakeConn()
        self.assertEqual(title_completion.title_finished(conn, FINISHED, 0, NOW), (0, []))
        self.assertEqual(conn.sql, [])


class Announce(unittest.TestCase):
    def test_events_wake_the_sender_and_moved_titles_go_to_their_people(self):
        sent = []
        with patch.object(as_events, "wake") as wake, patch.object(title_completion, "lists_moved", sent.append):
            title_completion.announce(1, [(20, ENTRY)])
            title_completion.announce(0, [])
        wake.assert_called_once()
        self.assertEqual(sent, [[(20, ENTRY)]])

    def test_without_the_hook_there_is_no_frame_and_no_error(self):  # утилиты: realtime у них нет
        with patch.object(as_events, "wake") as wake, patch.object(title_completion, "lists_moved", None):
            title_completion.announce(0, [(20, ENTRY)])
        wake.assert_not_called()


class StoringAPage(unittest.TestCase):
    def store(self, before, payload=FINISHED, sweep=True):
        order = []

        def answer(sql, params):
            if sql.startswith("SELECT episodes_total, airing_status FROM aviev_title_pages"):
                return ([before] if before else []), 1
            return [], 1
        conn = FakeConn(answer)
        conn.commit = lambda: order.append("commit")
        with patch.object(title_cache, "connect", return_value=conn), \
                patch.object(title_completion, "title_finished", return_value=(1, [(20, ENTRY)])) as finished, \
                patch.object(title_completion, "announce", side_effect=lambda *found: order.append(found)):
            title_cache.store_title(dict(payload), sweep=sweep)
        return finished, order

    def test_a_title_first_known_as_finished_is_checked_and_announced_after_commit(self):
        finished, order = self.store(None)
        self.assertEqual(finished.call_args.args[1:3], (FINISHED, 28))
        self.assertEqual(order, ["commit", (1, [(20, ENTRY)])])

    def test_a_status_or_count_change_is_checked_again_an_unchanged_page_is_not(self):
        self.assertTrue(self.store((28, "Currently Airing"))[0].called)
        self.assertTrue(self.store((24, "Finished Airing"))[0].called)
        finished, order = self.store((28, "Finished Airing"))
        self.assertFalse(finished.called)
        self.assertEqual(order, ["commit"])

    def test_an_airing_title_or_a_count_only_run_is_not_checked(self):
        self.assertFalse(self.store(None, dict(FINISHED, airing_status="Currently Airing"))[0].called)
        self.assertFalse(self.store(None, sweep=False)[0].called)


class AutoComplete(unittest.TestCase):
    def complete(self, entry, auto=True):
        """lists.auto_complete на поддельной базе: entry — (status, status_source) до правила или None."""
        state = {"entry": entry}

        def answer(sql, params):
            if sql.startswith("SELECT auto_add_lists"):
                return [(1 if auto else 0,)], 1
            if sql.startswith("SELECT user_id, mal_id, status"):
                row = state["entry"]
                return ([(20, 52991, *row, 0, "Frieren", "", AT, AT)] if row else []), 1
            if sql.startswith("INSERT INTO aviev_user_lists"):
                state["entry"] = (params[2], params[3])
            return [], 1
        conn = FakeConn(answer)
        return conn, lists.auto_complete(conn, 20, 52991, at=AT, title="Фрирен", poster_url="p.jpg")

    def test_empty_watching_or_planned_become_completed_with_the_time_of_watching(self):
        for entry in (None, ("watching", "auto"), ("watching", "manual"), ("planned", "manual")):
            with self.subTest(entry=entry):
                conn, result = self.complete(entry)
                self.assertEqual((result["status"], result["status_source"]), ("completed", "auto"))
                (_, params), = conn.statements("INSERT IGNORE INTO aviev_activity")
                self.assertEqual(params, (20, AT.date(), "complete", 52991, "", AT))  # день досмотра, не сегодня

    def test_manual_clear_and_final_statuses_stay(self):
        for entry in ((None, "manual"), ("completed", "manual"), ("dropped", "manual"), ("postponed", "manual"),
                      ("completed", "auto"), ("dropped", "auto")):
            with self.subTest(entry=entry):
                conn, result = self.complete(entry)
                self.assertIsNone(result)
                self.assertEqual(conn.statements("INSERT"), [])

    def test_auto_lists_off_change_nothing(self):
        conn, result = self.complete(("watching", "auto"), auto=False)
        self.assertIsNone(result)
        self.assertEqual(conn.statements("INSERT"), [])


class ListRules(unittest.TestCase):
    def run_event(self, entries, last=(28, "Finished Airing"), episode=28):
        statuses, kinds = [], []

        def answer(sql, params):
            if sql.startswith("SELECT episodes_total, airing_status FROM aviev_title_pages"):
                return ([last] if last else []), 1
            return [], 1
        # Одни подделки — и разделу, и общему правилу lists.auto_complete
        fakes = {"fetch_entry": Mock(side_effect=entries), "auto_add_enabled": Mock(return_value=True),
                 "upsert_status": Mock(side_effect=lambda *a, **k: statuses.append(k["status"])),
                 "record_event": Mock(side_effect=lambda *a, **k: kinds.append(k["kind"]))}
        patches = [patch.object(module, name, fake) for name, fake in fakes.items() for module in (progress_event, lists)]
        with patch.object(progress_event, "connect", return_value=FakeConn(answer)), \
                patch.object(progress_event, "upsert_episode_progress"), \
                patch.object(progress_event, "watched_seconds", return_value=0), patch.object(as_events, "wake"):
            for p in patches:
                p.start()
            try:
                event = progress_event.ProgressEventIn(mal_id=52991, episode_num=episode, seconds=1400,
                                                       duration=1420, episodes_total=99)  # число от плеера не в счёт
                progress_event.progress_event(event, {"id": 20})
            finally:
                for p in patches:
                    p.stop()
        return statuses, kinds

    def test_planned_gives_way_to_watching_and_then_to_completed(self):
        planned = {"status": "planned", "status_source": "manual"}
        watching = {"status": "watching", "status_source": "auto"}
        completed = {"status": "completed", "status_source": "auto"}
        statuses, kinds = self.run_event([planned, watching, completed, completed])
        self.assertEqual(statuses, ["watching", "completed"])
        self.assertEqual(kinds[:2], ["list_move", "complete"])  # из планов — перенос, а не добавление

    def test_manual_clear_completed_dropped_and_postponed_stay(self):
        for status in (None, "completed", "dropped", "postponed"):
            entry = {"status": status, "status_source": "manual"}
            self.assertEqual(self.run_event([entry, entry, entry])[0], [], status)

    def test_without_the_catalog_or_on_a_middle_episode_the_status_stays_watching(self):
        watching = {"status": "watching", "status_source": "auto"}
        self.assertEqual(self.run_event([watching, watching, watching], last=None)[0], [])
        self.assertEqual(self.run_event([watching, watching, watching], episode=27)[0], [])


if __name__ == "__main__":
    unittest.main()
