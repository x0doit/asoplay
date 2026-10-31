"""
Что решения панели делают с сайтом: скрытые тайтлы, выключенные источники и способы входа, состояние
для вкладок.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/admin_api: вход (core/tests/test_admin_api.py) собирает части по порядку.
"""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from core.classes import http_cache
from core.classes.jikan import catalog as jikan_catalog
from core.functions import site_settings, social_login
from core.modules import title_pages
from core import core as main


class SiteEffectTests(unittest.TestCase):
    """Что решения панели делают с сайтом."""

    def setUp(self):
        p = patch.object(site_settings, "state", site_settings._State())
        p.start()
        self.addCleanup(p.stop)
        site_settings.state.flags = site_settings.Flags(hidden=frozenset({2, 1535}), pinned=(52991,), adult=frozenset({7}))

    def test_hidden_titles_leave_title_lists_but_episode_rows_stay(self):
        async def native(path, params):
            return {"data": [{"mal_id": 1}, {"mal_id": 2}, {"mal_id": 3}], "has_next_page": False}
        with patch.object(jikan_catalog, "_native", side_effect=native):
            top = asyncio.run(jikan_catalog.catalog("top/anime", {}))
            episodes = asyncio.run(jikan_catalog.catalog("anime/5/episodes", {}))
        self.assertEqual([x["mal_id"] for x in top["data"]], [1, 3])
        self.assertEqual([x["mal_id"] for x in episodes["data"]], [1, 2, 3])

    def test_a_hidden_title_cannot_be_played_and_a_switched_off_source_finds_nothing(self):
        client = TestClient(main.app)
        resp = client.get("/src/search", params={"q": "Death Note", "source": "anilibria", "mal_id": 1535})
        self.assertEqual(resp.status_code, 451)
        site_settings.state.settings = site_settings.Settings(sources=({"id": "anilibria", "enabled": False, "order": 0},))
        with patch.object(main, "_source_call", side_effect=AssertionError("must not search")):
            self.assertEqual(client.get("/src/search", params={"q": "Naruto", "source": "anilibria"}).json(), [])

    def test_the_hidden_page_stays_for_people_but_not_for_search_engines(self):
        with patch.object(title_pages, "_load_cached", return_value={"slug": "death-note"}), \
                patch.object(title_pages, "_render_title_page", side_effect=lambda info: HTMLResponse("page")):
            resp = TestClient(main.app).get("/anime/1535-death-note", follow_redirects=False)
            self.assertEqual((resp.status_code, resp.headers["x-robots-tag"]), (451, "noindex"))
            self.assertEqual(TestClient(main.app).get("/anime/3-x", follow_redirects=False).status_code, 301)

    def test_russian_search_drops_hidden_titles(self):
        async def fetch(*args, **kwargs):
            return json.dumps([{"id": 1535, "russian": "Тетрадь смерти"}, {"id": 1, "russian": "Ковбой Бибоп"}]).encode(), \
                "application/json", 200
        with patch.object(http_cache, "cached_fetch", side_effect=fetch):
            found = TestClient(main.app).get("/shiki/search", params={"q": "тетрадь"}).json()
        self.assertEqual([x["id"] for x in found], [1])

    def test_the_page_state_says_everything_the_tabs_need(self):
        until = int(time.time()) + 60
        site_settings.state.settings = site_settings.Settings(notice={"text": "Профилактика", "until": until},
                                                              features={"comments": True, "password_login": False, "as_login": True})
        with patch.object(social_login, "enabled", return_value=True):
            state = site_settings.public_state()
        self.assertEqual(state, {"notice": {"text": "Профилактика", "until": until}, "hidden": [2, 1535], "adult": [7],
                                 "pinned": [52991], "login": {"social": True, "password": False},
                                 "sources": [s for s, _ in site_settings.SOURCES]})
        site_settings.state.settings = site_settings.Settings(notice={"text": "Профилактика", "until": int(time.time()) - 1})
        self.assertIsNone(site_settings.notice())

    def test_switched_off_sign_in_methods_are_refused(self):
        site_settings.state.settings = site_settings.Settings(features={"comments": True, "password_login": False, "as_login": False})
        client = TestClient(main.app)
        resp = client.post("/auth/login", json={"login": "a@example.test", "password": "p"})
        self.assertEqual((resp.status_code, resp.json()["detail"]), (403, "password_login_disabled"))
        with patch.object(social_login, "enabled", return_value=True):
            resp = client.get("/auth/as/start", params={"next": "/anime/1535"}, follow_redirects=False)
            self.assertEqual((resp.status_code, resp.headers["location"]), (302, "/anime/1535?as=disabled"))
            self.assertEqual(client.get("/health").json()["site"]["login"], {"social": False, "password": False})
