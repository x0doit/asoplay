"""
Приватность и чтение из базы: пачки запросов, значения по умолчанию, API приватности, карточка
тайтла.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/presence: вход (core/tests/test_presence.py) собирает части по порядку.
"""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.api.sections import account
from core.api.sections.account import settings as account_settings
from core.functions import presence, privacy, sessions

from core.tests.presence.lib import FakeCursor, FakeConn


class DatabaseTests(unittest.TestCase):
    def test_privacy_reads_in_chunks_and_fills_defaults_for_missing_rows(self):
        cursor = FakeCursor([(3, 1, 0, 1)])
        with patch.object(presence, "connect", return_value=FakeConn(cursor)):
            got = presence._privacy(range(1, 1503))
        self.assertEqual(got, {3: presence.Privacy(hide_presence=True, show_watching=False, hide_lists=True)})
        self.assertEqual([len(params) for _, params in cursor.queries], [1000, 502])
        self.assertIn("FROM aviev_privacy WHERE user_id IN (", cursor.queries[0][0])

    def test_nothing_to_look_up_means_no_query(self):
        with patch.object(presence, "connect", side_effect=AssertionError("no query expected")):
            self.assertEqual(presence._privacy([]), {})
            self.assertEqual(presence._titles([0]), {})
            self.assertEqual(presence._list_statuses(52991, []), {})


class PrivacyApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(account.router)
        app.dependency_overrides[sessions.current_user_required] = lambda: {"id": 5}
        self.client = TestClient(app)
        self.cursor = FakeCursor([])
        # Маршруты приватности пишут из раздела настроек аккаунта, читают — через core/functions/privacy.py
        for module in (account_settings, privacy):
            p = patch.object(module, "connect", side_effect=lambda: FakeConn(self.cursor))
            p.start()
            self.addCleanup(p.stop)

    def test_defaults_without_a_row(self):
        self.assertEqual(self.client.get("/account/privacy").json(),
                         {"hide_lists": False, "hide_activity": False, "hide_presence": False, "show_watching": False})

    def test_only_sent_fields_change(self):
        self.client.put("/account/privacy", json={"show_watching": True})
        sql, params = self.cursor.queries[0]
        self.assertIn("ON DUPLICATE KEY UPDATE show_watching=VALUES(show_watching), updated_at=VALUES(updated_at)", sql)
        self.assertEqual(params[:5], (5, 0, 0, 0, 1))
        self.cursor.queries.clear()
        self.client.put("/account/privacy", json={"hide_lists": True, "hide_activity": False})
        sql, _ = self.cursor.queries[0]
        self.assertIn("UPDATE hide_lists=VALUES(hide_lists), hide_activity=VALUES(hide_activity), updated_at", sql)
        self.assertNotIn("hide_presence=VALUES", sql)


class TitleCardTests(unittest.TestCase):
    def test_title_and_poster_come_from_the_title_page_cache(self):
        cursor = FakeCursor([(52991, "Фрирен", "Frieren", "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg"), (1, "", "Cowboy Bebop", "javascript:alert(1)"), (2, "", "", "")])
        with patch.object(presence, "connect", return_value=FakeConn(cursor)):
            got = presence._titles([52991, 1, 2])
        self.assertEqual(got, {52991: ("Фрирен", "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg"), 1: ("Cowboy Bebop", "")})
        self.assertIn("poster_url", cursor.queries[0][0])
