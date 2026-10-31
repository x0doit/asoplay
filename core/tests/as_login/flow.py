"""
Вход через соцсеть: начало с PKCE, возврат с кодом, отказы и ошибки, бан, сбой хранения; вход уже
вошедшего привязывает ту же сессию.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/as_login: вход (core/tests/test_as_login.py) собирает части по порядку.
"""
import base64
import hashlib
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.classes import as_api
from core.functions import sessions, social_login

from core.tests.as_login.lib import TOKENS


class FlowTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(social_login.router)
        self.client = TestClient(app, follow_redirects=False)
        self.inserted, self.logged_out = [], []
        self.exchange = lambda *_: {"tokens": dict(TOKENS), "user": {"id": 17, "banned": False}}
        patches = [
            patch.object(social_login, "enabled", return_value=True),
            patch.object(social_login, "_flows", social_login._FlowStore(600)),
            patch.object(social_login, "_insert", side_effect=lambda *a: self.inserted.append(a)),
            patch.object(sessions, "create_session", return_value="s" * 64),
            patch.object(as_api, "exchange_code", side_effect=lambda *a: self.exchange(*a)),
            patch.object(as_api, "logout", side_effect=lambda token, *_: self.logged_out.append(token)),
            patch.object(as_api, "base_url", return_value="https://social.test/api/v2"),
            patch.object(as_api, "client_id", return_value="cl_asoplay"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def start(self, next_path="/anime/52991"):
        resp = self.client.get("/auth/as/start", params={"next": next_path})
        self.assertEqual(resp.status_code, 302)
        return resp, parse_qs(urlsplit(resp.headers["location"]).query)

    def test_start_sends_the_browser_to_the_social_network_with_pkce(self):
        resp, query = self.start()
        self.assertTrue(resp.headers["location"].startswith("https://social.test/auth/app?"))
        self.assertEqual(query["redirect_uri"], ["http://testserver/auth/as/callback"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["client_id"], ["cl_asoplay"])
        state = query["state"][0]
        self.assertEqual(resp.cookies.get(social_login.FLOW_COOKIE), state)
        flow = social_login._flows.take(state)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertRegex(flow["verifier"], r"^[A-Za-z0-9._~\-]{43,128}$")
        self.assertEqual(flow["next"], "/anime/52991")

    def test_next_cannot_lead_to_another_site(self):
        for bad in ("//evil.test", "https://evil.test", "/\\evil.test", "evil", ""):
            _, query = self.start(bad)
            self.assertEqual(social_login._flows.take(query["state"][0])["next"], "/", bad)

    def callback(self, state, cookie_state=None, **params):
        if cookie_state:
            self.client.cookies.set(social_login.FLOW_COOKIE, cookie_state)
        return self.client.get("/auth/as/callback", params={"state": state, **params})

    def test_success_creates_a_session_and_returns_to_the_page(self):
        _, query = self.start()
        state = query["state"][0]
        resp = self.callback(state, state, code="a" * 64)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "/anime/52991")
        self.assertIn(f"{sessions.COOKIE_NAME}={'s' * 64}", resp.headers.get("set-cookie", ""))
        self.assertEqual(self.inserted, [("s" * 64, 17, TOKENS)])

    def test_a_state_works_once_and_only_with_its_own_cookie(self):
        _, query = self.start()
        state = query["state"][0]
        self.assertEqual(self.callback(state, "other-state", code="a" * 64).headers["location"], "/login?as=expired")
        self.client.cookies.clear()
        self.assertEqual(self.callback(state, None, code="a" * 64).headers["location"], "/login?as=expired")
        self.assertEqual(self.inserted, [])

    def test_a_replayed_state_is_rejected(self):
        _, query = self.start()
        state = query["state"][0]
        self.callback(state, state, code="a" * 64)
        self.assertEqual(self.callback(state, state, code="a" * 64).headers["location"], "/login?as=expired")

    def test_a_refusal_and_other_errors_return_to_the_login_page(self):
        _, query = self.start()
        state = query["state"][0]
        self.assertEqual(self.callback(state, state, error="access_denied").headers["location"], "/login?as=denied")
        _, query = self.start()
        state = query["state"][0]
        self.assertEqual(self.callback(state, state, error="server_error").headers["location"], "/login?as=error")

    def test_a_failed_exchange_is_reported(self):
        def fail(*_):
            raise as_api.ASApiError(400, "invalid_code", "Код входа недействителен")
        self.exchange = fail
        _, query = self.start()
        state = query["state"][0]
        self.assertEqual(self.callback(state, state, code="a" * 64).headers["location"], "/login?as=error")

    def test_a_banned_account_gets_no_session_and_its_social_session_ends(self):
        self.exchange = lambda *_: {"tokens": dict(TOKENS), "user": {"id": 17, "banned": True}}
        _, query = self.start()
        state = query["state"][0]
        self.assertEqual(self.callback(state, state, code="a" * 64).headers["location"], "/login?as=banned")
        self.assertEqual(self.logged_out, ["asa_access"])
        self.assertEqual(self.inserted, [])

    def test_a_storage_failure_opens_no_session_and_ends_the_social_one(self):
        revoked = []
        with patch.object(social_login, "_insert", side_effect=RuntimeError("table aviev_as_sessions is missing")), \
                patch.object(sessions, "revoke_session", side_effect=revoked.append), \
                self.assertLogs("asoplay.as_login", level="ERROR"):
            _, query = self.start()
            state = query["state"][0]
            resp = self.callback(state, state, code="a" * 64)
        self.assertEqual(resp.headers["location"], "/login?as=error")
        self.assertNotIn(sessions.COOKIE_NAME, resp.headers.get("set-cookie", ""))
        self.assertEqual(self.logged_out, ["asa_access"])
        self.assertEqual(revoked, ["s" * 64])

    def test_the_routes_are_hidden_while_the_feature_is_off(self):
        with patch.object(social_login, "enabled", return_value=False):
            self.assertEqual(self.client.get("/auth/as/start").status_code, 404)
            self.assertEqual(self.client.get("/auth/as/callback").status_code, 404)

    def signed_in(self, uid):
        """В AsoPlay уже вошли (по почте) аккаунтом uid."""
        self.client.cookies.set(sessions.COOKIE_NAME, "c" * 64)
        p = patch.object(sessions, "touch_session", side_effect=lambda token: {"id": uid} if token == "c" * 64 else None)
        p.start()
        self.addCleanup(p.stop)
        linked = []
        p = patch.object(social_login, "_link", side_effect=lambda *a: linked.append(a[:3]))
        p.start()
        self.addCleanup(p.stop)
        return linked

    def test_a_signed_in_person_links_the_same_session(self):
        linked = self.signed_in(17)
        _, query = self.start("/my/friends")
        state = query["state"][0]
        with patch.object(sessions, "create_session", side_effect=AssertionError("no new session expected")):
            resp = self.callback(state, state, code="a" * 64)
        self.assertEqual(resp.headers["location"], "/my/friends")
        self.assertNotIn(sessions.COOKIE_NAME, resp.headers.get("set-cookie", ""))
        self.assertEqual(linked, [("c" * 64, 17, TOKENS)])
        self.assertEqual(self.logged_out, [])

    def test_another_social_account_does_not_replace_the_signed_in_person(self):
        linked = self.signed_in(99)
        _, query = self.start("/my/friends?tab=online")
        state = query["state"][0]
        resp = self.callback(state, state, code="a" * 64)
        self.assertEqual(resp.headers["location"], "/my/friends?tab=online&as=other")
        self.assertNotIn(sessions.COOKIE_NAME, resp.headers.get("set-cookie", ""))
        self.assertEqual((linked, self.inserted, self.logged_out), ([], [], ["asa_access"]))

    def test_a_signed_in_person_sees_errors_on_the_page_they_came_from(self):
        self.signed_in(17)
        _, query = self.start("/my/friends")
        state = query["state"][0]
        self.assertEqual(self.callback(state, state, error="access_denied").headers["location"], "/my/friends?as=denied")

    def test_a_failed_link_ends_the_new_social_session(self):
        self.signed_in(17)
        with patch.object(social_login, "_link", side_effect=RuntimeError("database is down")),                 self.assertLogs("asoplay.as_login", level="ERROR"):
            _, query = self.start("/my/friends")
            state = query["state"][0]
            resp = self.callback(state, state, code="a" * 64)
        self.assertEqual(resp.headers["location"], "/my/friends?as=error")
        self.assertEqual(self.logged_out, ["asa_access"])
