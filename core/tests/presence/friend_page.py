"""
Страница друзей: заявки, «Возможные друзья», действия токеном человека; «Написать» уходит
в переписку соцсети.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/presence: вход (core/tests/test_presence.py) собирает части по порядку.
"""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.classes import as_api
from core.functions import presence, sessions, social_login

from core.tests.presence.lib import FakeClock, person, SocialFake


class FriendPageTests(unittest.TestCase):
    """Заявки, «Возможные друзья» и действия — как на странице друзей соцсети, токеном человека."""

    def setUp(self):
        app = FastAPI()
        app.include_router(presence.router)
        app.dependency_overrides[sessions.current_user_required] = lambda: {"id": 7}
        self.client = TestClient(app)
        self.client.cookies.set(sessions.COOKIE_NAME, "s" * 64)
        self.social = SocialFake([11], [person(11, where="site")])
        patches = [
            patch.object(presence, "presence", presence.Presence(clock=FakeClock())),
            patch.object(presence, "_friend_cache", {7: (10 ** 12, [11])}),
            patch.object(presence, "_privacy", return_value={}),
            patch.object(presence, "_titles", return_value={}),
            patch.object(social_login, "enabled", return_value=True),
            patch.object(social_login, "access_token", return_value="asa_token"),
            patch.object(as_api, "call", side_effect=self.social),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_friend_list_brings_the_request_counts(self):
        self.social.pending = {"incoming": [person(40)], "outgoing": [person(41), person(42)]}
        self.assertEqual(self.client.get("/account/friends").json()["requests"], {"incoming": 1, "outgoing": 2})

    def test_requests_come_with_the_city_and_when_they_were_sent(self):
        self.social.pending["incoming"] = [person(40, alias="kiba", location="Конoха, Страна Огня", requested_at=1793300000),
                                           person(41, deactivated="banned")]
        data = self.client.get("/account/friends/requests", params={"type": "incoming"}).json()
        self.assertEqual([(i["id"], i["profile_path"], i["location"], i["requested_at"]) for i in data["items"]],
                         [(40, "/@kiba", "Конoха, Страна Огня", 1793300000)])
        self.assertEqual(data["total"], 2)
        self.assertEqual(self.client.get("/account/friends/requests", params={"type": "all"}).status_code, 400)

    def test_suggestions_tell_how_many_friends_are_shared(self):
        self.social.suggestions = [person(50, mutual=3), person(51, mutual=0, location="Токио, Япония")]
        items = self.client.get("/account/friends/suggestions").json()["items"]
        self.assertEqual([(i["id"], i["mutual"], i["location"]) for i in items], [(50, 3, None), (51, 0, "Токио, Япония")])
        self.assertIn(("GET", "/friends/suggestions?limit=5", "asa_token"), self.social.requests)

    def test_actions_go_to_the_social_network_and_the_list_is_read_anew(self):
        self.assertEqual(self.client.post("/account/friends/40").json(), {"status": "outgoing", "friends_count": 5, "friend_requests": 1})
        self.assertNotIn(7, presence._friend_cache)
        self.client.post("/account/friends/41/accept")
        self.client.post("/account/friends/42/decline")
        self.client.delete("/account/friends/43")
        self.assertEqual(self.social.actions, [("POST", "/friends/40"), ("POST", "/friends/41/accept"),
                                               ("POST", "/friends/42/decline"), ("DELETE", "/friends/43")])
        self.assertEqual(self.client.post("/account/friends/7").status_code, 400)      # себя — нельзя

    def test_refusals_and_the_daily_limit_are_told_apart(self):
        for error, status, detail in ((as_api.ASApiError(403, "blacklisted"), 403, "blacklisted"),
                                      (as_api.ASApiError(409, "incoming_request"), 409, "incoming_request"),
                                      (as_api.ASApiError(409, "already_sent"), 409, "already_sent")):
            self.social.action_error = error
            resp = self.client.post("/account/friends/40")
            self.assertEqual((resp.status_code, resp.json()["detail"]), (status, detail))
        self.social.action_error = as_api.ASApiError(429, "rate_limited", retry_after=3600)
        resp = self.client.post("/account/friends/40")
        self.assertEqual((resp.status_code, resp.headers.get("retry-after")), (429, "3600"))


class WriteTests(unittest.TestCase):
    """«Написать» другу: сообщение уходит в переписку соцсети его токеном."""

    def setUp(self):
        app = FastAPI()
        app.include_router(presence.router)
        app.dependency_overrides[sessions.current_user_required] = lambda: {"id": 7}
        self.client = TestClient(app)
        self.client.cookies.set(sessions.COOKIE_NAME, "s" * 64)
        self.token = "asa_token"
        self.sent = []
        self.error = None
        self.forgotten = []

        def call(method, path, *, token=None, body=None, user_ip="", user_agent=""):
            if self.error:
                raise self.error
            self.sent.append((method, path, token, body))
            return {"id": 5121}

        patches = [
            patch.object(social_login, "enabled", return_value=True),
            patch.object(social_login, "access_token", side_effect=lambda *a: self.token),
            patch.object(social_login, "_delete", side_effect=self.forgotten.append),
            patch.object(as_api, "call", side_effect=call),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def write(self, peer, text):
        return self.client.post(f"/account/messages/{peer}", json={"text": text})

    def test_the_message_goes_to_the_social_dialog(self):
        resp = self.write(20, "  Привет! Смотрим Фрирен вместе?  ")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sent, [("POST", "/dialogs/20/messages", "asa_token", {"text": "Привет! Смотрим Фрирен вместе?"})])
        self.assertTrue(resp.json()["dialog_url"].endswith("/im?uid=20"))

    def test_empty_long_and_unlinked_messages_are_refused_here(self):
        self.assertEqual(self.write(20, "   ").status_code, 400)
        self.assertEqual(self.write(20, "я" * 4001).status_code, 400)
        self.token = None
        self.assertEqual(self.write(20, "Привет").status_code, 409)
        self.assertEqual(self.sent, [])

    def test_refusals_limits_and_outages_are_told_apart(self):
        self.error = as_api.ASApiError(403, "privacy", "Пишут только друзья")
        resp = self.write(20, "Привет")
        self.assertEqual((resp.status_code, resp.json()["detail"]), (403, "privacy"))
        self.error = as_api.ASApiError(429, "rate_limited", retry_after=30)
        resp = self.write(20, "Привет")
        self.assertEqual((resp.status_code, resp.headers.get("retry-after")), (429, "30"))
        self.error = as_api.ASApiError(401, "session_revoked")
        self.assertEqual(self.write(20, "Привет").status_code, 409)
        self.assertEqual(self.forgotten, ["s" * 64])
        self.error = as_api.ASApiError(503, "unavailable")
        with self.assertLogs("asoplay.presence", level="WARNING"):
            self.assertEqual(self.write(20, "Привет").status_code, 503)
