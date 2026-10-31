"""
Сторона соцсети: «кто вошёл в AsoPlay» для её сайта и переход на сайт под тем же человеком; адрес
возврата за прокси.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/as_login: вход (core/tests/test_as_login.py) собирает части по порядку.
"""
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.api.sections import social_login as social_routes
from core.classes import as_api, as_sessions
from core.functions import sessions

from core.tests.as_login.lib import make_request


class SocialSideTests(unittest.TestCase):
    """«Имя Фамилия, это вы?» на сайте соцсети: кто вошёл в AsoPlay и вход на сайт под ним."""

    def setUp(self):
        app = FastAPI()
        app.include_router(social_routes.router)
        self.user = None
        app.dependency_overrides[sessions.current_user_optional] = lambda: self.user
        self.client = TestClient(app, follow_redirects=False)
        patches = [
            patch.object(as_sessions, "enabled", return_value=True),
            patch.object(as_api, "base_url", return_value="https://social.test/api/v2"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_whoami_is_readable_by_the_social_site_only(self):
        self.user = {"id": 17, "name": "Сакура Харуно", "avatar": "https://social.test/a.jpg", "email": "s@example.test", "login": "sakura"}
        resp = self.client.get("/auth/as/whoami", headers={"Origin": "https://social.test"})
        self.assertEqual(resp.json(), {"logged_in": True, "user": {"id": 17, "name": "Сакура Харуно", "avatar": "https://social.test/a.jpg"}})
        self.assertEqual(resp.headers["access-control-allow-origin"], "https://social.test")
        self.assertEqual(resp.headers["access-control-allow-credentials"], "true")
        self.assertEqual(resp.headers["cache-control"], "no-store")
        other = self.client.get("/auth/as/whoami", headers={"Origin": "https://evil.test"})
        self.assertNotIn("access-control-allow-origin", other.headers)

    def test_whoami_for_a_guest(self):
        self.assertEqual(self.client.get("/auth/as/whoami").json(), {"logged_in": False})

    def test_whoami_sends_no_placeholder_avatar(self):
        from core.config import animesocial
        self.user = {"id": 17, "name": "Сакура Харуно", "avatar": animesocial.avatar_url(17, None)}
        self.assertIsNone(self.client.get("/auth/as/whoami").json()["user"]["avatar"])

    def go(self, **params):
        self.client.cookies.set(sessions.COOKIE_NAME, "s" * 64)
        return self.client.get("/auth/as/to-social", params=params).headers["location"]

    def test_to_social_signs_in_with_the_session_token(self):
        link = "https://social.test/auth/app/web?code=" + "c" * 64
        with patch.object(sessions, "touch_session", return_value={"id": 17}), \
                patch.object(as_sessions, "access_token", return_value="asa_live"), \
                patch.object(as_api, "web_login", return_value={"url": link, "expires_in": 60}) as web_login:
            self.assertEqual(self.go(next="/news", nonce="n" * 24), link)
        web_login.assert_called_once_with("asa_live", "/news", "n" * 24, "testclient", "testclient")

    def test_to_social_without_a_session_just_opens_the_page(self):
        with patch.object(sessions, "touch_session", return_value=None):
            self.assertEqual(self.go(next="/news"), "https://social.test/news")

    def test_to_social_drops_a_foreign_link_a_bad_nonce_and_a_bad_path(self):
        with patch.object(sessions, "touch_session", return_value={"id": 17}), \
                patch.object(as_sessions, "access_token", return_value="asa_live"), \
                patch.object(as_api, "web_login", return_value={"url": "https://evil.test/steal"}) as web_login:
            self.assertEqual(self.go(next="//evil.test", nonce="short"), "https://social.test/")
        web_login.assert_called_once_with("asa_live", "/", "", "testclient", "testclient")


class RedirectUriTests(unittest.TestCase):
    def test_the_scheme_comes_from_nginx_only_for_local_requests(self):
        headers = {"host": "play.animesocial.online", "x-forwarded-proto": "https"}
        self.assertEqual(social_routes.redirect_uri(make_request("127.0.0.1", headers)),
                         "https://play.animesocial.online/auth/as/callback")
        self.assertEqual(social_routes.redirect_uri(make_request("203.0.113.5", headers)),
                         "http://play.animesocial.online/auth/as/callback")

    def test_an_explicit_address_wins(self):
        with patch.dict(os.environ, {"AV_AS_REDIRECT_URI": "https://example.test/cb"}):
            self.assertEqual(social_routes.redirect_uri(make_request("127.0.0.1", {"host": "x"})), "https://example.test/cb")
