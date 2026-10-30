"""
AsoPlay — доверенный клиент: ключи в каждом запросе, адрес и браузер человека — только по его
действию; лимит соцсети и проверка сертификата.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/as_login: вход (core/tests/test_as_login.py) собирает части по порядку.
"""
import os
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from core.classes import as_api
from core.functions import social_login


class ClientHeaderTests(unittest.TestCase):
    """AsoPlay — доверенный клиент: ключи в каждом запросе, адрес и браузер человека — по его действию."""

    def capture(self, fn):
        seen = {}
        real_client = httpx.Client

        def handler(request):
            seen["headers"] = dict(request.headers)
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"ok": True, "data": {"done": True}})

        env = {"AV_AS_CLIENT_ID": "cl_asoplay", "AV_AS_CLIENT_SECRET": "cs_secret", "AV_AS_API_URL": "https://social.test/api/v2"}
        with patch.dict(os.environ, env),                 patch.object(as_api.httpx, "Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            self.assertTrue(as_api.configured())
            fn()
        return seen

    def test_keys_go_with_every_request_and_the_person_with_their_actions(self):
        seen = self.capture(lambda: as_api.logout("asa_live", "198.51.100.7", "Mozilla/5.0 Chrome/130"))
        headers = seen["headers"]
        self.assertEqual(seen["url"], "https://social.test/api/v2/auth/logout")
        self.assertEqual((headers["x-client-id"], headers["x-client-secret"]), ("cl_asoplay", "cs_secret"))
        self.assertEqual((headers["x-client-user-ip"], headers["x-client-user-agent"]), ("198.51.100.7", "Mozilla/5.0 Chrome/130"))
        self.assertEqual(headers["authorization"], "Bearer asa_live")

    def test_background_requests_do_not_invent_a_person(self):
        seen = self.capture(lambda: as_api.refresh("asr_old"))
        self.assertNotIn("x-client-user-ip", seen["headers"])
        self.assertNotIn("x-client-user-agent", seen["headers"])
        self.assertEqual(seen["body"], '{"refresh_token":"asr_old"}')

    def test_no_keys_means_the_feature_stays_off(self):
        with patch.dict(os.environ, {"AV_AS_CLIENT_ID": "", "AV_AS_CLIENT_SECRET": ""}):
            self.assertFalse(as_api.configured())
            self.assertFalse(social_login.enabled())

    def test_a_rate_limit_carries_the_wait_from_retry_after(self):
        real_client = httpx.Client
        env = {"AV_AS_CLIENT_ID": "cl_asoplay", "AV_AS_CLIENT_SECRET": "cs_secret", "AV_AS_API_URL": "https://social.test/api/v2"}
        answer = httpx.Response(429, headers={"Retry-After": "120"},
                                json={"ok": False, "error": {"code": "rate_limited", "message": "Слишком часто"}})
        with patch.dict(os.environ, env), \
                patch.object(as_api.httpx, "Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(lambda r: answer), **kw)), \
                self.assertRaises(as_api.ASApiError) as caught:
            as_api.password_login("sakura@example.test", "pw", "198.51.100.7", "Firefox")
        self.assertEqual((caught.exception.code, caught.exception.retry_after), ("rate_limited", 120))

    def test_the_certificate_check_uses_certifi_unless_told_otherwise(self):
        verify = []
        real_client = httpx.Client
        as_api._tls.cache_clear()
        self.addCleanup(as_api._tls.cache_clear)

        def client(**kw):
            verify.append(kw.get("verify"))
            return real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "data": {}})), **kw)

        env = {"AV_AS_CLIENT_ID": "cl_asoplay", "AV_AS_CLIENT_SECRET": "cs_secret", "AV_AS_API_URL": "https://social.test/api/v2"}
        with patch.object(as_api.httpx, "Client", side_effect=client):
            for ca in ("", "system"):
                with patch.dict(os.environ, {**env, "AV_AS_CA": ca}):
                    as_api.refresh("asr_old")
        self.assertIs(verify[0], True)
        self.assertIsInstance(verify[1], ssl.SSLContext)

    def test_a_missing_ca_file_is_an_outage_not_a_crash(self):
        as_api._tls.cache_clear()
        self.addCleanup(as_api._tls.cache_clear)
        with patch.dict(os.environ, {"AV_AS_CA": str(Path(__file__).with_name("no-such-ca.pem"))}),                 self.assertRaises(as_api.ASApiError) as caught:
            as_api.refresh("asr_old")
        self.assertEqual(caught.exception.code, "unavailable")
