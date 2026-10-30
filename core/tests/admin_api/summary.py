"""
Обзор в панели: люди, просмотры, топ тайтлов, здоровье источников, открытые жалобы; время просмотра
для обзора.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/admin_api: вход (core/tests/test_admin_api.py) собирает части по порядку.
"""
import unittest
from unittest.mock import patch

from core.admin import summary as admin_summary
from core.functions import source_health, watch_time

from core.tests.admin_api.lib import PanelCase


class SummaryTests(PanelCase):
    answers = {
        "COUNT(DISTINCT CASE": [(120, 14, 60, 95)],
        "HAVING MIN(created_at)": [(9,)],
        "COUNT(DISTINCT user_id, mal_id, meta)": [(31,)],
        "FROM aviev_watch_time": [(54_321,)],
        "COUNT(DISTINCT user_id) AS viewers": [(1535, 12), (52991, 7)],
        "FROM aviev_title_pages WHERE mal_id IN": [(1535, "Тетрадь смерти", "Death Note")],
        "FROM aviev_reports WHERE status = 'open'": [(4,)],
    }

    def test_the_overview_has_everything_the_panel_shows(self):
        with patch.object(admin_summary, "loaded_sources", lambda: ["yummy_anime", "anilibria"]), \
                patch.object(source_health, "_STATES", {}):
            source_health.mark_success("anilibria", 300)
            source_health.mark_failure("anilibria", "timeout")
            data = self.panel.call("GET", "/api/admin/summary").json()["data"]
        self.assertEqual(data["version"], admin_summary._version())
        self.assertNotEqual(data["version"], "unknown")
        self.assertEqual(data["users"], {"total": 120, "active_1d": 14, "active_7d": 60, "active_30d": 95, "new_7d": 9})
        self.assertEqual(data["watch"], {"episodes_1d": 31, "minutes_1d": 905})
        self.assertEqual(data["top_titles_7d"], [{"mal_id": 1535, "title": "Тетрадь смерти", "viewers": 12, "hidden": False},
                                                 {"mal_id": 52991, "title": "", "viewers": 7, "hidden": False}])
        anilibria = next(s for s in data["health"]["sources"] if s["id"] == "anilibria")
        self.assertEqual((anilibria["ok"], anilibria["fail_rate_24h"], anilibria["latency_ms"]), (True, 0.5, 300))
        dreamcast = next(s for s in data["health"]["sources"] if s["id"] == "dreamcast")
        self.assertFalse(dreamcast["ok"])                                    # не загрузился в этом процессе
        self.assertTrue(data["health"]["ok"])
        self.assertEqual(data["reports_open"], 4)
        self.assertEqual(set(data), {"version", "started_at", "health", "users", "watch", "top_titles_7d", "online",
                                     "reports_open"})


class WatchTimeTests(unittest.TestCase):
    def test_only_real_playback_counts(self):
        clock = [1000.0]
        with patch.object(watch_time, "_last_points", {}):
            def step(seconds_later, position):
                clock[0] += seconds_later
                return watch_time.watched_seconds(1, 1535, 3, position, clock=lambda: clock[0])
            self.assertEqual(step(0, 100), 0)        # первое событие — не с чем сравнить
            self.assertEqual(step(30, 130), 30)      # смотрел
            self.assertEqual(step(30, 190), 60)      # на скорости 2×
            self.assertEqual(step(30, 900), 0)       # перемотал вперёд
            self.assertEqual(step(30, 900), 0)       # пауза
            self.assertEqual(step(30, 600), 0)       # перемотал назад
            self.assertEqual(step(600, 630), 0)      # долгий перерыв
