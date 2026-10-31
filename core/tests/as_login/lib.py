"""
Общее для частей раздела as_login: токены из обмена кода, запрос с адресом и заголовками, сессии
соцсети в памяти вместо базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/as_login: вход (core/tests/test_as_login.py) собирает части по порядку.
"""
import time

from starlette.requests import Request

try:
    import cryptography  # noqa: F401
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

TOKENS = {"access_token": "asa_access", "refresh_token": "asr_refresh", "expires_in": 3600,
          "access_expires_at": int(time.time()) + 3600, "refresh_expires_at": int(time.time()) + 86400 * 90,
          "session_id": 41}


def make_request(peer, headers):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/auth/as/start", "scheme": "http",
                    "server": ("127.0.0.1", 8787), "headers": raw, "client": (peer, 50000)})


class FakeStore:
    """aviev_as_sessions в памяти вместо базы."""

    def __init__(self, access_expires_at):
        self.rows = {"s1": {"access": "asa_old", "access_expires_at": access_expires_at, "refresh": "asr_old"}}

    def load(self, session):
        row = self.rows.get(session)
        return dict(row) if row else None

    def update(self, session, tokens):
        self.rows[session] = {"access": tokens["access_token"], "access_expires_at": tokens["access_expires_at"],
                              "refresh": tokens["refresh_token"]}

    def delete(self, session):
        self.rows.pop(session, None)
