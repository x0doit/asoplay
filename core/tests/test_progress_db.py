"""
«Продолжить с m:ss» на настоящей базе: дальняя точка серии (seconds) не уменьшается, last_seconds — где человек
остановился в последний раз, а у строки без last_seconds (записанной до миграции) вместо него — seconds.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Пишет в базу, поэтому по умолчанию пропускается: идёт только при AV_TESTS_DB=1 и только на базе этой машины, иначе
отказ. Человек — временный аккаунт (core/tests/temp_account.py); всё удаляется вместе с ним. Нужна миграция
core/migrations/2026-11-05_progress_last_seconds.sql.
"""
import os
import unittest

from core.classes.clock import utc_now
from core.classes.db import connect
from core.config import config, env
from core.functions import progress
from core.tests.temp_account import TempAccount
from core.tests.test_lists_sweep_db import _local

MAL = 52991
OLD_ROW = ("INSERT INTO aviev_episode_progress (user_id, mal_id, episode_num, seconds, duration, updated_at) "
           "VALUES (%s, %s, %s, %s, %s, %s)")


class LastSecondsOnTheDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("AV_TESTS_DB") != "1":
            raise unittest.SkipTest("пишет в базу: только с AV_TESTS_DB=1 (core/tests/README.md)")
        env.load()
        host = config.db_settings()["host"]
        if not _local(host):
            raise RuntimeError(f"AV_TESTS_DB=1, но база не на этой машине ({host}) — отказ")
        # Серия 2 — строка, записанная до миграции: без last_seconds
        old = [(MAL, 2, 600, 1440, utc_now().replace(microsecond=0))]
        cls.account = TempAccount([(OLD_ROW, old)], handle="asoplay-guard-resume")
        cls.enterClassContext(cls.account)
        cls.uid = cls.account.user_id

    def test_the_furthest_point_stays_and_last_seconds_is_where_the_viewer_stopped(self):
        with connect() as conn:
            progress.upsert_episode_progress(conn, self.uid, MAL, 1, 800, 1440)
            progress.upsert_episode_progress(conn, self.uid, MAL, 1, 300, 1440)  # отмотал назад и ушёл
            conn.commit()
        with connect() as conn:
            rows = {r["episode_num"]: r for r in progress.progress_rows_for_title(conn, self.uid, MAL)}
        self.assertEqual((rows[1]["seconds"], rows[1]["last_seconds"]), (800, 300))
        self.assertEqual((rows[2]["seconds"], rows[2]["last_seconds"]), (600, 600))  # старая строка — дальняя точка


if __name__ == "__main__":
    unittest.main()
