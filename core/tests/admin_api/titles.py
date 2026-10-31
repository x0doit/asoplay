"""
Флаги тайтлов из панели: список, поиск по ссылке, скрыть, закрепить, 18+, сброс кеша; медленный каталог
не держит панель.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/admin_api: вход (core/tests/test_admin_api.py) собирает части по порядку.
"""
import asyncio
import json
import time
from datetime import datetime
from unittest.mock import patch
from urllib.parse import quote

from core.admin import reports as admin_reports
from core.admin import titles as admin_titles
from core.classes import http_cache
from core.classes.jikan import catalog as jikan_catalog
from core.functions import site_settings
from core.modules import title_pages

from core.tests.admin_api.lib import PanelCase


class TitleTests(PanelCase):
    answers = {
        "FROM aviev_title_flags f LEFT JOIN": [(1535, "Тетрадь смерти", 1, 0, 0, "Жалоба правообладателя", 3,
                                               datetime(2026, 10, 30, 12, 0))],
        "SELECT hidden, pinned, adult, note, updated_by, updated_at FROM aviev_title_flags":
            [(0, 1, 0, "", 3, datetime(2026, 10, 30, 12, 0))],
    }

    def setUp(self):
        super().setUp()
        self.announced = []
        for target, value in ((site_settings, "listeners"), (site_settings.state, "load")):
            p = patch.object(target, value, [self.announced.append] if value == "listeners" else (lambda: None))
            p.start()
            self.addCleanup(p.stop)

    def test_titles_the_moderators_touched_are_listed(self):
        data = self.panel.call("GET", "/api/admin/titles").json()["data"]
        self.assertEqual(data["items"][0], {
            "mal_id": 1535, "title": "Тетрадь смерти", "hidden": True, "pinned": False, "adult": False,
            "note": "Жалоба правообладателя", "updated_by": 3, "updated_at": 1793361600,
            "url": f"{admin_reports.SITE_URL}/anime/1535"})

    def test_a_title_is_found_by_its_link_on_asoplay(self):
        async def lookup(mal_id):
            return {"title_ru": "Тетрадь смерти"} if mal_id == 1535 else None
        with patch.object(title_pages, "lookup", side_effect=lookup), patch.object(admin_titles, "_flags_for", return_value={}):
            path = "/api/admin/titles?q=" + quote("https://play.animesocial.online/anime/1535-death-note/")
            items = self.panel.call("GET", path).json()["data"]["items"]
            self.assertEqual([(i["mal_id"], i["title"], i["hidden"]) for i in items], [(1535, "Тетрадь смерти", False)])
            self.assertEqual(self.panel.call("GET", "/api/admin/titles?q=2").json()["data"]["items"], [])

    def test_only_the_sent_flags_change_and_the_tabs_hear_it(self):
        async def lookup(mal_id):
            return {"title_ru": "Тетрадь смерти"}
        with patch.object(title_pages, "lookup", side_effect=lookup):
            resp = self.panel.call("PUT", "/api/admin/titles/1535", {"hidden": True, "note": "Жалоба\tправообладателя"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(saved_row := self.cursor.sql("INSERT INTO aviev_title_flags")[0][1][:6],
                         (1535, 1, 1, 0, "Жалоба правообладателя", 3))
        del saved_row
        saved = self.cursor.sql("INSERT INTO aviev_title_flags")[0][1]
        self.assertEqual(saved[:6], (1535, 1, 1, 0, "Жалоба правообладателя", 3))    # закрепление осталось
        self.assertEqual(json.loads(self.cursor.sql("INSERT INTO aviev_admin_audit")[0][1][3]),
                         {"hidden": True, "note": "Жалоба правообладателя"})
        self.assertEqual(self.announced, [{"titles"}])
        self.assertTrue(resp.json()["data"]["hidden"] and resp.json()["data"]["pinned"])

    def test_the_panel_sends_all_four_fields_and_only_changes_are_logged(self):
        async def lookup(mal_id):
            return {"title_ru": "Тетрадь смерти"}
        with patch.object(title_pages, "lookup", side_effect=lookup):
            resp = self.panel.call("PUT", "/api/admin/titles/1535", {"hidden": False, "pinned": True, "adult": True, "note": ""})
            self.assertEqual(json.loads(self.cursor.sql("INSERT INTO aviev_admin_audit")[0][1][3]), {"adult": True})
            self.assertEqual(self.announced, [{"titles"}])
            self.cursor.queries.clear()
            same = self.panel.call("PUT", "/api/admin/titles/1535", {"hidden": False, "pinned": True, "adult": False, "note": ""})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self.cursor.sql("INSERT"), [])                                   # ничего не поменялось
        self.assertEqual(self.announced, [{"titles"}])
        self.assertEqual((same.json()["data"]["pinned"], same.json()["data"]["updated_by"]), (True, 3))

    def test_a_slow_catalog_does_not_hold_the_panel(self):
        async def slow(mal_id):
            await asyncio.sleep(0.3)
            return {"title_ru": "Тетрадь смерти"}

        async def scenario():
            started = time.monotonic()
            name = await admin_titles._title_name(1535)
            elapsed = time.monotonic() - started
            await asyncio.sleep(0.4)                                               # фоновый запрос доходит до конца
            return name, elapsed
        with patch.object(admin_titles, "CATALOG_WAIT", 0.05), patch.object(title_pages, "lookup", side_effect=slow):
            name, elapsed = asyncio.run(scenario())
        self.assertEqual(name, "")
        self.assertLess(elapsed, 0.25)

    def test_a_title_that_is_not_in_mal_cannot_be_flagged(self):
        async def lookup(mal_id):
            return None
        with patch.object(title_pages, "lookup", side_effect=lookup):
            resp = self.panel.call("PUT", "/api/admin/titles/2", {"hidden": True})
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (404, "title_not_found"))

    def test_resetting_the_cache_drops_the_card_and_the_page(self):
        with patch.object(jikan_catalog, "forget_title", return_value=2) as card, \
                patch.object(http_cache, "forget_shiki_title", return_value=True), \
                patch.object(title_pages, "forget") as page:
            resp = self.panel.call("POST", "/api/admin/titles/1535/purge")
        self.assertEqual(resp.json(), {"ok": True, "data": {"mal_id": 1535, "removed": 3}})
        card.assert_called_once_with(1535)
        page.assert_called_once_with(1535)
        self.assertEqual(self.cursor.sql("INSERT INTO aviev_admin_audit")[0][1][1:3], ("title.purge", "title:1535"))
