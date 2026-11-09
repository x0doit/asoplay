"""
Cookie сессии: снимается с теми же атрибутами, что ставится, — Secure, HttpOnly, SameSite и путь.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Starlette по умолчанию снимает cookie без Secure и HttpOnly, поэтому на сайте с https выход отвечал Set-Cookie без
Secure, хотя cookie ставилась с ним. Проверка сравнивает оба заголовка при включённом и выключенном Secure.
"""
import unittest
from unittest.mock import patch

from starlette.responses import Response

from core.functions import sessions


def attributes(action, secure: bool) -> dict:
    """Атрибуты Set-Cookie, которые action пишет в ответ при COOKIE_SECURE = secure (имена — строчными)."""
    resp = Response()
    with patch.object(sessions.config, "COOKIE_SECURE", secure):
        action(resp)
    header = resp.headers["set-cookie"]
    assert header.startswith(sessions.COOKIE_NAME + "="), header
    parts = [part.strip() for part in header.split(";")[1:]]
    return {part.split("=", 1)[0].lower(): part.split("=", 1)[1] if "=" in part else True for part in parts}


class SessionCookie(unittest.TestCase):
    def test_clearing_carries_the_flags_of_setting(self):
        for secure in (True, False):
            set_flags = attributes(lambda resp: sessions.set_session_cookie(resp, "t" * 64), secure)
            clear_flags = attributes(sessions.clear_session_cookie, secure)
            for name in ("secure", "httponly", "samesite", "path"):
                self.assertEqual(clear_flags.get(name), set_flags.get(name), (name, secure))
            self.assertEqual(clear_flags.get("secure"), True if secure else None)
            self.assertEqual(clear_flags.get("max-age"), "0")


if __name__ == "__main__":
    unittest.main()
