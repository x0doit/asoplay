"""
Где пишутся события: серия — только когда впервые стала досмотренной (серии, отмеченные сами, — без событий),
досмотр тайтла — только авто-правилом последней серии (ручное «Просмотрено» — нет), оценка — с её временем;
после commit отправитель просыпается.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from core.api.sections.account import import_storage, ratings
from core.api.sections.lists import entries, progress_event
from core.functions import as_events, progress
from core.tests.as_events.lib import AT, FakeConn, linked_answer

USER = {"id": 20}


class FirstDone(unittest.TestCase):
    def setUp(self):
        p = patch.object(as_events, "record")
        self.record = p.start()
        self.addCleanup(p.stop)

    def upsert(self, before, seconds, duration, episode=6):
        conn = FakeConn(linked_answer(set(), {(20, 52991, episode): before} if before else {}))
        done = progress.upsert_episode_progress(conn, 20, 52991, episode, seconds, duration, updated_at=AT)
        return done, conn

    def test_an_episode_gives_one_event_the_first_time_it_is_done(self):
        done, _ = self.upsert(None, 1300, 1400)
        self.assertTrue(done)
        self.record.assert_called_once()
        self.assertEqual(self.record.call_args.args[1:4], (20, "episode", 52991))
        self.assertEqual(self.record.call_args.kwargs, {"episode": 6, "at": AT})

    def test_later_saves_of_a_done_episode_and_unfinished_ones_give_none(self):
        self.assertFalse(self.upsert((1350, 1400), 1400, 1400)[0])  # уже было досмотрено
        self.assertFalse(self.upsert((100, 1400), 700, 1400)[0])    # ещё не досмотрено
        self.record.assert_not_called()

    def test_episodes_marked_done_because_a_later_one_is_done_give_no_events(self):
        _, conn = self.upsert(None, 1300, 1400, episode=6)
        stamped = [p[2] for sql, p in conn.statements("INSERT INTO aviev_episode_progress") if p[2] != 6]
        self.assertEqual(stamped, [1, 2, 3, 4, 5])
        self.assertEqual([c.kwargs["episode"] for c in self.record.call_args_list], [6])


class Completed(unittest.TestCase):
    def test_the_last_episode_rule_gives_completed_and_wakes_the_sender_after_commit(self):
        order = []
        conn = FakeConn()
        conn.commit = lambda: order.append("commit")
        with patch.object(progress_event, "connect", return_value=conn), \
                patch.object(progress_event, "upsert_episode_progress"), \
                patch.object(progress_event, "fetch_entry", side_effect=[None, None, {"status": "completed"}]), \
                patch.object(progress_event, "auto_add_enabled", return_value=True), \
                patch.object(progress_event, "upsert_status"), patch.object(progress_event, "record_event"), \
                patch.object(progress_event, "watched_seconds", return_value=0), \
                patch.object(as_events, "record") as record, \
                patch.object(as_events, "wake", side_effect=lambda: order.append("wake")):
            event = progress_event.ProgressEventIn(mal_id=52991, episode_num=12, seconds=1400, duration=1420,
                                                   episodes_total=12)
            progress_event.progress_event(event, USER)
        self.assertEqual([c.args[1:4] for c in record.call_args_list], [(20, "completed", 52991)])
        self.assertEqual(order, ["commit", "wake"])

    def test_a_manual_completed_status_gives_no_event(self):
        with patch.object(entries, "mal_id_exists", AsyncMock(return_value=True)), \
                patch.object(entries, "connect", return_value=FakeConn()), \
                patch.object(entries, "fetch_entry", return_value=None), \
                patch.object(entries, "upsert_status"), patch.object(entries, "record_event"), \
                patch.object(as_events, "record") as record:
            asyncio.run(entries.set_status(52991, entries.ListItemIn(status="completed"), USER))
        record.assert_not_called()  # решено 31.10: очки — за настоящий досмотр, не за клик


class Rating(unittest.TestCase):
    def test_a_rating_is_an_event_with_its_score_and_the_same_time_as_the_rating(self):
        conn = FakeConn()
        with patch.object(ratings, "connect", return_value=conn), patch.object(ratings, "record_event"), \
                patch.object(as_events, "record") as record, patch.object(as_events, "wake") as wake:
            ratings.rating_set(52991, ratings.RatingIn(score=8), USER)
        (_, params), = conn.statements("INSERT INTO aviev_title_ratings")
        self.assertEqual(record.call_args.args[1:4], (20, "rating", 52991))
        self.assertEqual(record.call_args.kwargs, {"score": 8, "at": params[3]})
        wake.assert_called_once()

    def test_removing_a_rating_sends_nothing(self):
        with patch.object(ratings, "connect", return_value=FakeConn()), patch.object(ratings, "record_event"), \
                patch.object(as_events, "record") as record:
            ratings.rating_clear(52991, USER)
        record.assert_not_called()


class Imports(unittest.TestCase):
    def test_the_import_from_the_browser_writes_progress_without_events(self):
        text = inspect.getsource(import_storage)
        self.assertNotIn("as_events", text)                # импорт — не просмотр на AsoPlay
        self.assertNotIn("upsert_episode_progress", text)  # его прогресс пишется своим INSERT IGNORE
