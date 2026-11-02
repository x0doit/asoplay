"""
Очередь событий: запись в транзакции действия, только людям с живой сессией соцсети, кадр с ключом тайтла и
временем строго по UTC, чтение старейших с отсевом старше окна, удаление подтверждённого.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from core.classes import as_sessions
from core.functions import as_events
from core.tests.as_events.lib import AT, FakeConn, linked_answer, row


class Record(unittest.TestCase):
    def setUp(self):
        p = patch.object(as_sessions, "enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_an_event_goes_through_the_action_s_connection_and_is_not_committed_by_itself(self):
        conn = FakeConn(linked_answer({20}))
        self.assertTrue(as_events.record(conn, 20, "episode", 52991, episode=6, at=AT))
        self.assertEqual(conn.statements("INSERT IGNORE INTO aviev_as_events"),
                         [("INSERT IGNORE INTO aviev_as_events (user_id, type, mal_id, episode, at) "
                           "VALUES (%s, %s, %s, %s, %s)", (20, "episode", 52991, 6, AT))])
        self.assertEqual(conn.commits, 0)  # commit — у действия: откатится оно — откатится и событие

    def test_completed_has_no_episode_and_a_new_rating_replaces_the_unsent_one(self):
        conn = FakeConn(linked_answer({20}))
        as_events.record(conn, 20, "completed", 52991, episode=12, at=AT)
        as_events.record(conn, 20, "rating", 52991, score=9, at=AT)
        once, rating = conn.statements("INSERT")
        self.assertEqual(once[1], (20, "completed", 52991, 0, AT))
        self.assertIn("ON DUPLICATE KEY UPDATE score = VALUES(score), at = VALUES(at)", rating[0])
        self.assertEqual(rating[1], (20, 52991, 9, AT))

    def test_people_without_a_live_social_session_get_no_events(self):
        conn = FakeConn(linked_answer(set()))
        self.assertFalse(as_events.record(conn, 31, "rating", 52991, score=7))
        self.assertEqual(conn.statements("INSERT"), [])
        session, params = conn.sql[0]
        self.assertIn("`refresh_expires_at` > %s", session)  # живая — токен обновления не истёк

    def test_nothing_is_written_while_sign_in_through_the_social_network_is_off(self):
        conn = FakeConn(linked_answer({20}))
        with patch.object(as_sessions, "enabled", return_value=False):
            self.assertFalse(as_events.record(conn, 20, "episode", 52991, episode=1))
        self.assertEqual(conn.sql, [])

    def test_a_database_error_is_not_swallowed_so_the_action_is_not_saved_without_its_event(self):
        def answer(sql, params):
            if sql.startswith("INSERT"):
                raise RuntimeError("deadlock")
            return linked_answer({20})(sql, params)
        with self.assertRaises(RuntimeError):
            as_events.record(FakeConn(answer), 20, "episode", 52991, episode=1)


class Frame(unittest.TestCase):
    def test_frames_carry_the_title_key_the_episode_or_the_score_and_utc_unix_time(self):
        at = int(AT.replace(tzinfo=timezone.utc).timestamp())
        self.assertEqual(as_events.frame(row(1)),
                         {"user_id": 20, "type": "episode", "object": "asoplay:anime:52991", "episode": 6, "at": at})
        self.assertEqual(as_events.frame(row(2, "rating", score=9))["score"], 9)
        self.assertEqual(set(as_events.frame(row(3, "completed"))), {"user_id", "type", "object", "at"})

    def test_unix_time_is_utc_whatever_the_server_s_time_zone(self):
        self.assertEqual(as_events.unix(datetime(1970, 1, 2)), 86400)  # наивный .timestamp() взял бы местный пояс
        self.assertEqual(as_events.unix(datetime(2026, 10, 30, 21, 0)),
                         int(datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc).timestamp()))


class TakeAndDrop(unittest.TestCase):
    def test_take_drops_what_the_social_network_would_no_longer_count_and_reads_the_oldest_first(self):
        stored = (7, 20, "rating", 52991, 0, 8, AT)
        conn = FakeConn(lambda sql, params: ([stored], 1) if sql.startswith("SELECT") else ([], 2))
        with patch.object(as_events, "connect", return_value=conn), \
                patch.object(as_events, "utc_now", return_value=datetime(2026, 11, 8, 12, 0)):
            rows = as_events.take()
        (drop, cutoff), (read, limit) = conn.sql
        self.assertEqual((drop, cutoff), ("DELETE FROM aviev_as_events WHERE at < %s", (datetime(2026, 10, 31, 12, 0),)))
        self.assertTrue(read.endswith("ORDER BY id LIMIT %s") and limit == (500,))
        self.assertEqual(rows, [as_events.Row(7, 20, "rating", 52991, 0, 8, AT)])
        self.assertEqual(conn.commits, 1)

    def test_drop_sent_keeps_a_rating_replaced_after_it_was_sent(self):
        conn = FakeConn()
        with patch.object(as_events, "connect", return_value=conn):
            as_events.drop_sent([row(1), row(2, "completed"), row(3, "rating", score=9)])
        self.assertEqual(conn.sql, [
            ("DELETE FROM aviev_as_events WHERE type <> 'rating' AND id IN (%s, %s)", (1, 2)),
            ("DELETE FROM aviev_as_events WHERE id = %s AND score = %s AND at = %s", (3, 9, AT)),
        ])
        self.assertEqual(conn.commits, 1)
