"""
Лимит попыток входа по паролю: скользящее окно, адрес посетителя за прокси, вход через соцсеть.
Живую базу не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from starlette.requests import Request
from core.classes import as_api, db
from core.functions import rate_limit, social_login


def make_request(peer="203.0.113.5", headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "headers": raw, "client": (peer, 50000)})


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SlidingWindowTests(unittest.TestCase):
    def test_blocks_after_the_limit_and_recovers_after_the_window(self):
        clock = FakeClock()
        window = rate_limit.SlidingWindow(3, 60, clock=clock)
        for _ in range(3):
            self.assertEqual(window.retry_after("k"), 0)
            window.hit("k")
        self.assertEqual(window.retry_after("k"), 60)
        clock.now += 59.5
        self.assertEqual(window.retry_after("k"), 1)
        clock.now += 1
        self.assertEqual(window.retry_after("k"), 0)

    def test_keys_are_separate_and_reset_clears_one(self):
        window = rate_limit.SlidingWindow(1, 60, clock=FakeClock())
        window.hit("a")
        window.hit("b")
        self.assertGreater(window.retry_after("a"), 0)
        window.reset("a")
        self.assertEqual(window.retry_after("a"), 0)
        self.assertGreater(window.retry_after("b"), 0)

    def test_key_count_is_capped(self):
        window = rate_limit.SlidingWindow(1, 60, max_keys=3, clock=FakeClock())
        for key in "abcde":
            window.hit(key)
        self.assertLessEqual(len(window._hits), 3)
        self.assertIn("e", window._hits)


class ClientIpTests(unittest.TestCase):
    def test_proxy_headers_are_trusted_only_from_loopback(self):
        self.assertEqual(rate_limit.client_ip(make_request("127.0.0.1", {"X-Real-IP": "198.51.100.7"})), "198.51.100.7")
        self.assertEqual(rate_limit.client_ip(make_request("203.0.113.5", {"X-Real-IP": "198.51.100.7"})), "203.0.113.5")

    def test_forwarded_for_uses_the_address_added_by_nginx(self):
        request = make_request("127.0.0.1", {"X-Forwarded-For": "10.0.0.1, 198.51.100.8"})
        self.assertEqual(rate_limit.client_ip(request), "198.51.100.8")

    def test_a_malformed_header_falls_back_to_the_peer(self):
        self.assertEqual(rate_limit.client_ip(make_request("127.0.0.1", {"X-Real-IP": "evil"})), "127.0.0.1")


class LoginLimitTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.banned = 0
        patches = [
            patch.object(db, "_login_by_ip", rate_limit.SlidingWindow(20, db.LOGIN_WINDOW, clock=self.clock)),
            patch.object(db, "_login_by_identity", rate_limit.SlidingWindow(8, db.LOGIN_WINDOW, clock=self.clock)),
            patch.object(db, "_lookup_user", side_effect=self.lookup),
            patch.object(db, "_verify_password", side_effect=lambda plain, stored, salt: plain == "right-password"),
            patch.object(db, "_introspect_schema", return_value={"status_col": "banned"}),
            patch.object(db, "_serialize_user", side_effect=lambda row: {"id": row["id_col"]}),
            patch.object(db, "create_session", return_value="t" * 64),
            # Без ключей соцсети пароль сверяется по её таблице
            patch.object(social_login, "enabled", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def lookup(self, identity):
        if identity.lower() != "sakura":
            return None
        return {"id_col": 7, "login_col": "sakura", "hash_col": "stored", "salt_col": None, "status_col": self.banned}

    def attempt(self, login, password, ip="198.51.100.9"):
        request = make_request("127.0.0.1", {"X-Real-IP": ip})
        try:
            db.auth_login(db.LoginIn(login=login, password=password), request, Response())
            return 200, {}
        except HTTPException as exc:
            return exc.status_code, exc.headers or {}

    def test_eight_wrong_passwords_lock_the_login_even_for_the_right_one(self):
        for _ in range(8):
            self.assertEqual(self.attempt("sakura", "wrong")[0], 401)
        status, headers = self.attempt("Sakura", "right-password", ip="198.51.100.10")
        self.assertEqual(status, 429)
        self.assertEqual(headers.get("Retry-After"), str(db.LOGIN_WINDOW))

    def test_twenty_failures_from_one_address_lock_that_address_only(self):
        for i in range(20):
            self.assertEqual(self.attempt(f"user{i}", "wrong")[0], 401)
        self.assertEqual(self.attempt("sakura", "right-password")[0], 429)
        self.assertEqual(self.attempt("sakura", "right-password", ip="198.51.100.11")[0], 200)

    def test_a_successful_login_resets_the_login_counter(self):
        for _ in range(7):
            self.attempt("sakura", "wrong")
        self.assertEqual(self.attempt("sakura", "right-password")[0], 200)
        for _ in range(7):
            self.assertEqual(self.attempt("sakura", "wrong")[0], 401)
        self.assertEqual(self.attempt("sakura", "right-password")[0], 200)

    def test_the_lock_expires_after_the_window(self):
        for _ in range(8):
            self.attempt("sakura", "wrong")
        self.assertEqual(self.attempt("sakura", "right-password")[0], 429)
        self.clock.now += db.LOGIN_WINDOW + 1
        self.assertEqual(self.attempt("sakura", "right-password")[0], 200)

    def test_a_ban_is_revealed_only_with_the_right_password(self):
        self.banned = 1
        self.assertEqual(self.attempt("sakura", "wrong")[0], 401)
        self.assertEqual(self.attempt("sakura", "right-password")[0], 403)



class SocialPasswordLoginTests(unittest.TestCase):
    """С ключами соцсети форма почты входит через её POST /auth/login: пароль проверяет соцсеть, а у человека
    появляется сессия AsoPlay в соцсети, как после входа кнопкой."""

    def setUp(self):
        self.clock = FakeClock()
        self.banned = False
        self.social_calls = []
        self.answer = self.social_network
        self.inserted, self.logged_out, self.revoked, self.ended = [], [], [], []
        self.profile = {"id": 7, "name": "Сакура Харуно"}
        patches = [
            patch.object(db, "_login_by_ip", rate_limit.SlidingWindow(20, db.LOGIN_WINDOW, clock=self.clock)),
            patch.object(db, "_login_by_identity", rate_limit.SlidingWindow(8, db.LOGIN_WINDOW, clock=self.clock)),
            patch.object(db, "_lookup_user", side_effect=AssertionError("the social network checks the password")),
            patch.object(social_login, "enabled", return_value=True),
            patch.object(as_api, "password_login", side_effect=lambda *a: self.answer(*a)),
            patch.object(as_api, "logout", side_effect=lambda token, *_: self.logged_out.append(token)),
            patch.object(db, "create_session", return_value="t" * 64),
            patch.object(db, "revoke_session", side_effect=self.revoked.append),
            patch.object(social_login, "_insert", side_effect=lambda session, uid, tokens: self.inserted.append((session, uid))),
            patch.object(social_login, "_session_user", side_effect=lambda s: self.profile if s == "t" * 64 else None),
            patch.object(social_login, "end_session", side_effect=lambda s, *_: self.ended.append(s)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def social_network(self, email, password, user_ip, user_agent):
        self.social_calls.append((email, user_ip, user_agent))
        if (email, password) != ("sakura@example.test", "right-password"):
            raise as_api.ASApiError(400, "invalid_credentials", "Неверная почта или пароль")
        return {"tokens": {"access_token": "asa_x", "refresh_token": "asr_x", "expires_in": 3600},
                "user": {"id": 7, "banned": self.banned}}

    def attempt(self, login, password, ip="198.51.100.9"):
        request = make_request("127.0.0.1", {"X-Real-IP": ip, "User-Agent": "Mozilla/5.0 Firefox/131"})
        response = Response()
        try:
            body = db.auth_login(db.LoginIn(login=login, password=password), request, response)
            return 200, body, response
        except HTTPException as exc:
            return exc.status_code, exc.headers or {}, response

    def test_the_social_network_checks_the_password_and_the_session_is_linked(self):
        status, body, response = self.attempt("sakura@example.test", "right-password")
        self.assertEqual((status, body), (200, {"ok": True, "user": self.profile}))
        self.assertIn(f"{db.COOKIE_NAME}={'t' * 64}", response.headers.get("set-cookie", ""))
        self.assertEqual(self.inserted, [("t" * 64, 7)])
        self.assertEqual(self.social_calls, [("sakura@example.test", "198.51.100.9", "Mozilla/5.0 Firefox/131")])

    def test_wrong_passwords_count_towards_the_local_limit_too(self):
        for _ in range(8):
            self.assertEqual(self.attempt("sakura@example.test", "wrong")[0], 401)
        calls = len(self.social_calls)
        status, headers, _ = self.attempt("Sakura@example.test", "right-password", ip="198.51.100.10")
        self.assertEqual((status, headers.get("Retry-After")), (429, str(db.LOGIN_WINDOW)))
        self.assertEqual(len(self.social_calls), calls)          # заблокированная попытка до соцсети не доходит

    def test_the_social_network_limit_comes_with_its_wait(self):
        def limited(*_):
            raise as_api.ASApiError(429, "rate_limited", "Слишком много попыток", retry_after=120)
        self.answer = limited
        status, headers, _ = self.attempt("sakura@example.test", "right-password")
        self.assertEqual((status, headers.get("Retry-After")), (429, "120"))

    def test_a_ban_is_revealed_only_with_the_right_password_and_ends_the_social_session(self):
        self.banned = True
        self.assertEqual(self.attempt("sakura@example.test", "wrong")[0], 401)
        self.assertEqual(self.attempt("sakura@example.test", "right-password")[0], 403)
        self.assertEqual((self.logged_out, self.inserted), (["asa_x"], []))

    def test_an_outage_or_rejected_keys_mean_the_network_is_unavailable(self):
        for error in (as_api.ASApiError(503, "unavailable"), as_api.ASApiError(401, "invalid_client")):
            def fail(*_, error=error):
                raise error
            self.answer = fail
            with self.assertLogs("asoplay.as_login", level="WARNING"):
                self.assertEqual(self.attempt("sakura@example.test", "right-password")[0], 503)
        self.assertEqual(self.inserted, [])

    def test_an_account_missing_from_the_asoplay_database_gets_no_session(self):
        self.profile = None
        with self.assertLogs("asoplay.as_login", level="ERROR"):
            self.assertEqual(self.attempt("sakura@example.test", "right-password")[0], 503)
        self.assertEqual((self.ended, self.revoked), (["t" * 64], ["t" * 64]))


if __name__ == "__main__":
    unittest.main()
