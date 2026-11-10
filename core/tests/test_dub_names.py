"""
Реестр имён озвучек для выбора любимой (core/functions/dub_names.py, GET /src/dub-names): какие имена попадают
в реестр, одна вставка на окно, предел строк, список с частыми именами и его кеш. Без базы и без таймеров.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import json
import unittest
from unittest.mock import patch

from core.api.sections.sources import names as names_section
from core.functions import dub_names
from core.tests.as_events.lib import FakeConn


def voice(name):
    return {"name": name, "norm": "", "kind": "voice", "key": "k"}


class Fresh(unittest.TestCase):
    """Каждая проверка — с пустой памятью реестра и без настоящего таймера."""

    def setUp(self):
        self.timers = []
        timer = patch.object(dub_names.threading, "Timer", side_effect=self._timer)
        timer.start()
        self.addCleanup(timer.stop)
        self._reset()
        self.addCleanup(self._reset)

    def _timer(self, delay, callback):
        test = self

        class Timer:
            daemon = False

            def start(self):
                test.timers.append((delay, callback))
        return Timer()

    @staticmethod
    def _reset():
        dub_names._pending.clear()
        dub_names._timer = None
        dub_names._listing = (0.0, [])


class CleanNameTests(unittest.TestCase):
    def test_foreign_text_loses_control_and_invisible_characters_and_is_capped(self):
        self.assertEqual(dub_names.clean_name("Ani\x00Dub\nTV‮​ "), "Ani Dub TV")
        self.assertEqual(dub_names.clean_name(" JAM "), "JAM")
        self.assertEqual(len(dub_names.clean_name("я" * 500)), dub_names.NAME_MAX)
        self.assertEqual(dub_names.clean_name(None), "")


class NoteTests(Fresh):
    def test_only_voice_overs_count_and_the_voice_prefix_goes(self):
        dub_names.note([voice("Озвучка AniLibria"), {"name": "Субтитры AniLibria", "kind": "sub"}, voice("—"),
                        "not a dub", voice("AniLibria")])
        self.assertEqual(dub_names._pending, {"anilibria": ["AniLibria", 2]})

    def test_one_insert_per_window_however_many_answers(self):
        for _ in range(3):
            dub_names.note([voice("JAM")])
        self.assertEqual([delay for delay, _ in self.timers], [dub_names.WINDOW])
        self.assertIs(self.timers[0][1], dub_names.flush)

    def test_new_names_stop_at_the_pending_limit_but_known_ones_still_count(self):
        with patch.object(dub_names, "PENDING_MAX", 2):
            dub_names.note([voice("A1"), voice("B2"), voice("C3"), voice("A1")])
        self.assertEqual(dub_names._pending, {"a1": ["A1", 2], "b2": ["B2", 1]})


class FlushTests(Fresh):
    def flush(self, rows_in_table):
        conn = FakeConn(lambda sql, params: ([(rows_in_table,)], 1) if sql.startswith("SELECT COUNT") else ([], 1))
        with patch.object(dub_names, "connect", return_value=conn):
            dub_names.flush()
        return conn

    def test_pending_names_go_in_one_upsert_and_the_window_reopens(self):
        dub_names.note([voice("AniLibria"), voice("JAM"), voice("JAM")])
        conn = self.flush(10)
        (sql, params), = conn.statements("INSERT INTO aviev_dub_names")
        self.assertIn("ON DUPLICATE KEY UPDATE name = VALUES(name), seen = seen + VALUES(seen)", sql)
        self.assertEqual([params[i:i + 3] for i in range(0, len(params), 4)], [("anilibria", "AniLibria", 1),
                                                                           ("jam", "JAM", 2)])
        self.assertEqual((dub_names._pending, dub_names._timer, conn.commits), ({}, None, 1))
        self.assertEqual(conn.statements("DELETE"), [])

    def test_over_the_row_limit_the_rarest_rows_go(self):
        dub_names.note([voice("JAM")])
        conn = self.flush(dub_names.ROWS_MAX + 3)
        self.assertEqual(conn.statements("DELETE"),
                         [("DELETE FROM aviev_dub_names ORDER BY seen, last_seen LIMIT %s", (3,))])

    def test_a_database_failure_is_logged_and_never_raised(self):
        dub_names.note([voice("JAM")])
        with patch.object(dub_names, "connect", side_effect=OSError("down")), \
                self.assertLogs("asoplay.dubs", "WARNING") as logs:
            dub_names.flush()
        self.assertIn("OSError", logs.output[0])

    def test_nothing_pending_means_no_connection(self):
        with patch.object(dub_names, "connect") as connect:
            dub_names.flush()
        connect.assert_not_called()


class ListingTests(Fresh):
    def test_frequent_first_then_seed_names_not_yet_seen_and_one_query_per_ttl(self):
        conn = FakeConn(lambda sql, params: ([("AniLibria.TV",), ("Bad\x00Name",), ("Kansai",)], 3))
        with patch.object(dub_names, "connect", return_value=conn) as connect:
            first = dub_names.listing()
            second = dub_names.listing()
        # «AniLibria.TV» сравнивается как «anilibria tv» — частое «AniLibria» из SEED остаётся
        self.assertEqual(first, ["AniLibria.TV", "Bad Name", "Kansai", *dub_names.SEED])
        self.assertEqual(second, first)
        self.assertEqual(connect.call_count, 1)

    def test_seed_names_already_in_the_table_are_not_repeated(self):
        conn = FakeConn(lambda sql, params: ([("JAM",), ("AniDub",)], 2))
        with patch.object(dub_names, "connect", return_value=conn):
            got = dub_names.listing()
        self.assertEqual(got[:2], ["JAM", "AniDub"])
        self.assertEqual(sorted(got), sorted(set(got)))
        self.assertEqual(len(got), len(dub_names.SEED))

    def test_without_the_database_the_seed_comes_back_uncached(self):
        with patch.object(dub_names, "connect", side_effect=OSError("down")), self.assertLogs("asoplay.dubs", "WARNING"):
            self.assertEqual(dub_names.listing(), list(dub_names.SEED))
        self.assertEqual(dub_names._listing, (0.0, []))

    def test_the_route_answers_a_cacheable_list(self):
        with patch.object(dub_names, "listing", return_value=["JAM", "AniDub"]):
            resp = asyncio.run(names_section.dub_names_list())
        self.assertEqual(json.loads(resp.body), ["JAM", "AniDub"])
        self.assertEqual(resp.headers["cache-control"], "public, max-age=600")


if __name__ == "__main__":
    unittest.main()
