"""
Друзья: порядок по тому, где человек, скрытое присутствие, кеш и пачки запросов, мёртвая сессия,
друзья на странице тайтла.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/presence: вход (core/tests/test_presence.py) собирает части по порядку.
"""
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.classes import as_api, as_sessions
from core.functions import presence, sessions

from core.tests.presence.lib import NOW, FakeClock, person, SocialFake


class FriendsTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(presence.router)
        app.dependency_overrides[sessions.current_user_required] = lambda: {"id": 7}
        self.client = TestClient(app)
        self.client.cookies.set(sessions.COOKIE_NAME, "s" * 64)
        self.store = presence.Presence(clock=FakeClock())
        self.privacy = {}
        self.statuses = {}
        self.token = "asa_token"
        self.social = SocialFake([11, 12, 13, 14, 15], [
            person(11, alias="sora", where="site"),
            person(12, alias="bad alias!", last_seen=NOW - 300),
            person(13, where="mobile"),
            person(14, last_seen=NOW - 30, sex=2, avatar=False),
            person(15, last_seen=NOW - 9000, deactivated="banned"),
        ])
        self.forgotten = []
        patches = [
            patch.object(presence, "presence", self.store),
            patch.object(presence, "_friend_cache", {}),
            patch.object(presence, "_privacy", side_effect=lambda ids: {u: self.privacy[u] for u in ids if u in self.privacy}),
            patch.object(presence, "_titles", side_effect=lambda ids: {52991: ("Фрирен", "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg")} if 52991 in set(ids) else {}),
            patch.object(presence, "_list_statuses", side_effect=lambda mal_id, ids: {u: s for u, s in self.statuses.items() if u in set(ids)}),
            patch.object(as_sessions, "enabled", return_value=True),
            patch.object(as_sessions, "access_token", side_effect=lambda *a: self.token),
            patch.object(as_sessions, "_delete", side_effect=self.forgotten.append),
            patch.object(as_api, "call", side_effect=self.social),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_without_a_social_session_there_is_nothing_to_show(self):
        self.token = None
        self.assertEqual(self.client.get("/account/friends").json(),
                         {"connected": False, "items": [], "total": 0, "online": 0})
        self.assertEqual(self.client.get("/account/friends/title/52991").json(),
                         {"connected": False, "items": [], "total": 0})
        self.assertEqual(self.social.requests, [])

    def test_friends_are_ordered_by_where_they_are(self):
        self.store.tab(13, 1, True, 52991, "Фрирен")
        self.privacy[13] = presence.Privacy(show_watching=True)
        self.store.tab(14, 1, True)
        data = self.client.get("/account/friends").json()
        self.assertTrue(data["connected"])
        self.assertEqual([f["id"] for f in data["items"]], [13, 14, 11, 12])   # заблокированный 15 выпал
        watching, here, social, away = data["items"]
        self.assertEqual(watching["watching"], {"mal_id": 52991, "title": "Фрирен", "poster": "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg", "path": "/anime/52991"})
        self.assertEqual(watching["social"], "mobile")
        self.assertTrue(here["on_asoplay"])
        self.assertIsNone(here["watching"])
        self.assertIsNone(here["avatar"])
        self.assertEqual(social["profile_path"], "/@sora")
        self.assertEqual(social["avatar"], "https://social.test/a/11m.jpg")
        self.assertEqual((social["social"], social["on_asoplay"]), ("site", False))
        self.assertEqual(away["profile_path"], "/@profile12")           # кривой alias в ссылку не попадает
        self.assertEqual((away["last_seen"], away["social"]), (NOW - 300, None))
        self.assertEqual((data["total"], data["online"]), (4, 3))
        self.assertIn(("GET", "/users/7/friends/ids", "asa_token"), self.social.requests)

    def test_hidden_presence_hides_the_person_here_too(self):
        self.store.tab(12, 1, True, 52991)
        self.privacy[12] = presence.Privacy(hide_presence=True, show_watching=True)
        away = next(f for f in self.client.get("/account/friends").json()["items"] if f["id"] == 12)
        self.assertEqual((away["on_asoplay"], away["watching"]), (False, None))

    def test_friend_ids_are_cached_and_people_are_asked_in_chunks(self):
        self.social.friends = list(range(1000, 2200))
        self.client.get("/account/friends")
        self.client.get("/account/friends")
        paths = [p for _, p, _ in self.social.requests]
        self.assertEqual(paths.count("/users/7/friends/ids"), 1)
        chunks = [len(parse_qs(urlsplit(p).query)["ids"][0].split(",")) for p in paths if p.startswith("/users?")]
        self.assertEqual(chunks, [500, 500, 200, 500, 500, 200])

    def test_a_dead_social_session_unlinks_and_an_outage_is_reported(self):
        self.social.error = as_api.ASApiError(401, "session_revoked")
        self.assertFalse(self.client.get("/account/friends").json()["connected"])
        self.assertEqual(self.forgotten, ["s" * 64])
        self.social.error = as_api.ASApiError(503, "unavailable")
        with self.assertLogs("asoplay.presence", level="WARNING"):
            self.assertEqual(self.client.get("/account/friends").status_code, 503)
        self.assertEqual(self.forgotten, ["s" * 64])

    def test_title_shows_who_watches_now_and_whose_lists_have_it(self):
        self.store.tab(12, 1, True, 52991)
        self.privacy[12] = presence.Privacy(show_watching=True)
        self.store.tab(13, 1, True, 52991)                                      # смотрит, но не разрешил показывать
        self.store.tab(11, 1, True, 1)                                          # смотрит другой тайтл
        self.statuses = {13: "planned", 14: "completed", 15: "watching", 11: "dropped"}
        self.privacy[11] = presence.Privacy(hide_lists=True)
        data = self.client.get("/account/friends/title/52991").json()
        self.assertEqual([(f["id"], f["now"], f["status"]) for f in data["items"]],
                         [(12, True, None), (14, False, "completed"), (13, False, "planned")])
        self.assertEqual(data["total"], 3)
        asked = [p for _, p, _ in self.social.requests if p.startswith("/users?")]
        self.assertEqual(asked, ["/users?ids=12,13,14,15"])            # 15 заблокирован — соцсеть его не вернула

    def test_title_without_friends_on_it_asks_nobody_and_rejects_bad_ids(self):
        self.assertEqual(self.client.get("/account/friends/title/52991").json(),
                         {"connected": True, "items": [], "total": 0})
        self.assertFalse(any(p.startswith("/users?") for _, p, _ in self.social.requests))
        self.assertEqual(self.client.get("/account/friends/title/0").status_code, 400)
