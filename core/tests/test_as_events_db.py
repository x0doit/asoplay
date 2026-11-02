"""
Очередь событий соцсети на настоящей базе: событие живёт и откатывается вместе с транзакцией действия, серия
пишется один раз, новая оценка заменяет неотправленную, а подтверждение старой её не удаляет.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Пишет в базу, поэтому по умолчанию пропускается: идёт только при AV_TESTS_DB=1 и только на базе этой машины, иначе
отказ. Человек — временный аккаунт (core/tests/temp_account.py) со своей временной строкой в aviev_as_sessions; всё
удаляется вместе с аккаунтом. Нужна миграция core/migrations/2026-11-02_as_events.sql.
"""
import os
import secrets
import time
import unittest
from unittest.mock import patch

from core.classes import as_sessions
from core.classes.clock import utc_now
from core.classes.db import connect
from core.config import config, env
from core.functions import as_events
from core.tests.temp_account import TempAccount
from core.tests.test_lists_sweep_db import _local

MAL = 52991


def _query(sql: str, params: tuple) -> list[tuple]:
    """Свежим соединением — то, что действительно сохранено."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return list(cur.fetchall())


class QueueOnTheDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("AV_TESTS_DB") != "1":
            raise unittest.SkipTest("пишет в базу: только с AV_TESTS_DB=1 (core/tests/README.md)")
        env.load()
        host = config.db_settings()["host"]
        if not _local(host):
            raise RuntimeError(f"AV_TESTS_DB=1, но база не на этой машине ({host}) — отказ")
        cls.account = TempAccount([], handle="asoplay-guard-events")
        cls.enterClassContext(cls.account)
        cls.uid = cls.account.user_id
        now = utc_now().replace(microsecond=0)
        with connect() as conn:
            conn.cursor().execute(
                "INSERT INTO aviev_as_sessions (session_token, user_id, access_token, access_expires_at, refresh_token,"
                " refresh_expires_at, created_at, updated_at) VALUES (%s, %s, 'x', %s, 'x', %s, %s, %s)",
                (secrets.token_hex(32), cls.uid, int(time.time()) + 600, int(time.time()) + 3600, now, now))
            conn.commit()
        enabled = patch.object(as_sessions, "enabled", return_value=True)
        enabled.start()
        cls.addClassCleanup(enabled.stop)

    def mine(self) -> list[tuple]:
        return _query("SELECT type, episode, score FROM aviev_as_events WHERE user_id = %s ORDER BY id", (self.uid,))

    def tearDown(self):
        with connect() as conn:
            conn.cursor().execute("DELETE FROM aviev_as_events WHERE user_id = %s", (self.uid,))
            conn.commit()

    def test_an_event_is_rolled_back_with_its_action(self):
        with connect() as conn:
            self.assertTrue(as_events.record(conn, self.uid, "rating", MAL, score=7))
            conn.rollback()
        self.assertEqual(self.mine(), [])
        with connect() as conn:
            as_events.record(conn, self.uid, "rating", MAL, score=7)
            conn.commit()
        self.assertEqual(self.mine(), [("rating", 0, 7)])

    def test_an_episode_is_written_once(self):
        for _ in range(2):
            with connect() as conn:
                as_events.record(conn, self.uid, "episode", MAL, episode=6)
                conn.commit()
        self.assertEqual(self.mine(), [("episode", 6, None)])

    def test_a_rating_replaced_after_sending_stays_until_its_own_confirmation(self):
        with connect() as conn:
            as_events.record(conn, self.uid, "rating", MAL, score=7)
            conn.commit()
        sent = [r for r in as_events.take() if r.user_id == self.uid]
        with connect() as conn:
            as_events.record(conn, self.uid, "rating", MAL, score=9)
            conn.commit()
        as_events.drop_sent(sent)                                 # подтвердили старую оценку
        self.assertEqual(self.mine(), [("rating", 0, 9)])
        as_events.drop_sent([r for r in as_events.take() if r.user_id == self.uid])
        self.assertEqual(self.mine(), [])


if __name__ == "__main__":
    unittest.main()
