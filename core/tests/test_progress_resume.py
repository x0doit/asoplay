"""
«Продолжить с m:ss»: у позиции серии две секунды — дальняя точка (seconds, по ней «досмотрено») и где человек
остановился (last_seconds, для кнопки). Без базы: соединение — подделка, которая записывает запросы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from core.api.sections.account import progress as progress_section
from core.functions import progress
from core.tests.as_events.lib import FakeConn

AT = datetime(2026, 11, 5, 12, 0, 0)


class LastSecondsTests(unittest.TestCase):
    def test_a_save_keeps_the_furthest_point_and_records_where_the_viewer_stopped(self):
        conn = FakeConn(lambda sql, params: ([(800, 1440)] if sql.startswith("SELECT seconds, duration") else [], 1))
        progress.upsert_episode_progress(conn, 20, 52991, 1, 300, 1440, updated_at=AT)  # отмотал назад и ушёл
        (sql, params), = conn.statements("INSERT INTO aviev_episode_progress")
        self.assertIn("seconds=GREATEST(seconds, VALUES(seconds))", sql)
        self.assertIn("last_seconds=VALUES(last_seconds)", sql)
        self.assertEqual(params, (20, 52991, 1, 300, 1440, AT, 300))

    def test_rows_give_where_the_viewer_stopped_and_old_rows_their_furthest_point(self):
        rows = [(1, 800, 1440, AT, 300), (2, 600, 1440, AT, None)]
        out = progress.progress_rows_for_title(FakeConn(lambda sql, params: (rows, len(rows))), 20, 52991)
        self.assertEqual([(r["episode_num"], r["seconds"], r["last_seconds"]) for r in out], [(1, 800, 300), (2, 600, 600)])

    def test_the_progress_endpoint_answers_both_seconds(self):
        rows = [(1, 800, 1440, AT, 300)]
        with patch.object(progress_section, "connect", return_value=FakeConn(lambda sql, params: (rows, 1))):
            out = progress_section.progress_for_title(52991, {"id": 20})
        self.assertEqual(out, [{"episode_num": 1, "seconds": 800, "duration": 1440, "updated_at": AT.isoformat(),
                                "last_seconds": 300}])


if __name__ == "__main__":
    unittest.main()
