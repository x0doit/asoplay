"""
Токены соцсети: одно обновление на параллельные запросы, мёртвая сессия и сбой соцсети, привязка,
выход, шифрование в базе.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/as_login: вход (core/tests/test_as_login.py) собирает части по порядку.
"""
import base64
import os
import threading
import time
import unittest
from unittest.mock import patch

from core.classes import as_api, token_box
from core.functions import social_login

from core.tests.as_login.lib import HAS_CRYPTO, TOKENS, FakeStore


class AccessTokenTests(unittest.TestCase):
    def use(self, store):
        for name, fn in (("_load", store.load), ("_update", store.update), ("_delete", store.delete)):
            p = patch.object(social_login, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def test_a_fresh_token_is_used_as_is(self):
        self.use(FakeStore(int(time.time()) + 3000))
        with patch.object(as_api, "refresh") as refresh:
            self.assertEqual(social_login.access_token("s1"), "asa_old")
        refresh.assert_not_called()

    def test_an_expiring_token_is_refreshed_once_for_parallel_requests(self):
        store = FakeStore(int(time.time()) + 10)
        self.use(store)
        calls = []

        def slow_refresh(token, *_):
            calls.append(token)
            time.sleep(0.2)
            return {"access_token": "asa_new", "refresh_token": "asr_new", "access_expires_at": int(time.time()) + 3600}

        results = []
        with patch.object(as_api, "refresh", side_effect=slow_refresh):
            threads = [threading.Thread(target=lambda: results.append(social_login.access_token("s1"))) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(calls, ["asr_old"])
        self.assertEqual(results, ["asa_new"] * 4)
        self.assertEqual(store.rows["s1"]["refresh"], "asr_new")

    def test_a_dead_social_session_drops_the_link(self):
        store = FakeStore(int(time.time()) - 5)
        self.use(store)
        with patch.object(as_api, "refresh", side_effect=as_api.ASApiError(401, "session_revoked")):
            self.assertIsNone(social_login.access_token("s1"))
        self.assertNotIn("s1", store.rows)

    def test_a_social_network_outage_keeps_the_link(self):
        store = FakeStore(int(time.time()) - 5)
        self.use(store)
        with patch.object(as_api, "refresh", side_effect=as_api.ASApiError(503, "unavailable")):
            self.assertIsNone(social_login.access_token("s1"))
        self.assertIn("s1", store.rows)


class LinkTests(unittest.TestCase):
    def test_linking_replaces_the_previous_social_session(self):
        calls = []
        with patch.object(social_login, "access_token", return_value="asa_previous"),                 patch.object(as_api, "logout", side_effect=lambda token, *_: calls.append(("logout", token))),                 patch.object(social_login, "_delete", side_effect=lambda s: calls.append(("delete", s))),                 patch.object(social_login, "_insert", side_effect=lambda s, uid, t: calls.append(("insert", s, uid))):
            social_login._link("c1", 17, dict(TOKENS))
        self.assertEqual(calls, [("logout", "asa_previous"), ("delete", "c1"), ("insert", "c1", 17)])

    def test_only_a_dead_social_session_is_forgotten(self):
        with patch.object(social_login, "_delete") as delete:
            self.assertFalse(social_login.forget_if_dead("c1", as_api.ASApiError(503, "unavailable")))
            delete.assert_not_called()
            self.assertTrue(social_login.forget_if_dead("c1", as_api.ASApiError(401, "unauthorized")))
            delete.assert_called_once_with("c1")

    def test_the_outcome_joins_an_existing_query(self):
        self.assertEqual(social_login._with_as("/my/friends", "other"), "/my/friends?as=other")
        self.assertEqual(social_login._with_as("/anime/1?ep=2", "error"), "/anime/1?ep=2&as=error")


class EndSessionTests(unittest.TestCase):
    def test_logout_ends_the_social_session_and_forgets_it(self):
        deleted = []
        with patch.object(token_box, "available", return_value=True), \
                patch.object(social_login, "access_token", return_value="asa_live"), \
                patch.object(social_login, "_delete", side_effect=deleted.append), \
                patch.object(as_api, "logout") as logout:
            social_login.end_session("s1")
        logout.assert_called_once_with("asa_live", "", "")
        self.assertEqual(deleted, ["s1"])

    def test_a_social_network_failure_does_not_block_logout(self):
        deleted = []
        with patch.object(token_box, "available", return_value=True), \
                patch.object(social_login, "access_token", return_value="asa_live"), \
                patch.object(social_login, "_delete", side_effect=deleted.append), \
                patch.object(as_api, "logout", side_effect=as_api.ASApiError(503, "unavailable")):
            social_login.end_session("s1")
        self.assertEqual(deleted, ["s1"])

    def test_a_database_failure_does_not_raise(self):
        with patch.object(token_box, "available", return_value=False), \
                patch.object(social_login, "_delete", side_effect=RuntimeError("db down")), \
                self.assertLogs("asoplay.as_login", level="ERROR"):
            social_login.end_session("s1")


@unittest.skipUnless(HAS_CRYPTO, "the cryptography package is not installed")
class TokenBoxTests(unittest.TestCase):
    KEY = base64.urlsafe_b64encode(b"k" * 32).decode()

    def test_round_trip_and_context_binding(self):
        with patch.dict(os.environ, {"AV_TOKEN_KEY": self.KEY}):
            blob = token_box.seal("asr_secret", "row1:refresh")
            self.assertNotIn(b"asr_secret", blob)
            self.assertEqual(token_box.unseal(blob, "row1:refresh"), "asr_secret")
            self.assertIsNone(token_box.unseal(blob, "row2:refresh"))
            tampered = blob[:-1] + bytes([blob[-1] ^ 1])
            self.assertIsNone(token_box.unseal(tampered, "row1:refresh"))
        other = base64.urlsafe_b64encode(b"z" * 32).decode()
        with patch.dict(os.environ, {"AV_TOKEN_KEY": other}):
            self.assertIsNone(token_box.unseal(blob, "row1:refresh"))

    def test_no_key_means_no_encryption(self):
        with patch.dict(os.environ, {"AV_TOKEN_KEY": ""}):
            self.assertFalse(token_box.available())
            self.assertFalse(social_login.enabled())
        with patch.dict(os.environ, {"AV_TOKEN_KEY": "short"}):
            self.assertFalse(token_box.available())
