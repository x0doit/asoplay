"""
Общее для частей раздела presence: часы, курсор и соединение-подделки, человек из ответа соцсети,
соцсеть-подделка.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/presence: вход (core/tests/test_presence.py) собирает части по порядку.
"""
import time
from urllib.parse import parse_qs, urlsplit

from core.functions import presence_batch

NOW = int(time.time())
SITE = presence_batch.SITE_URL


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((" ".join(sql.split()), tuple(params)))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def person(uid, *, alias=None, where=None, last_seen=None, sex=1, avatar=True, **extra):
    return {"id": uid, "alias": alias, "first_name": f"Имя{uid}", "last_name": f"Фамилия{uid}", "sex": sex,
            "avatar": {"small": f"https://social.test/a/{uid}s.jpg", "medium": f"https://social.test/a/{uid}m.jpg"}
            if avatar else None,
            "online": where in ("site", "mobile"), "online_where": where, "online_client": None,
            "last_seen": last_seen, "verified": False, "elite": False, "deactivated": None, **extra}


class SocialFake:
    """GET /users/{id}/friends/ids и GET /users?ids= соцсети."""

    def __init__(self, friends, people):
        self.friends = friends
        self.people = {p["id"]: p for p in people}
        self.requests = []
        self.error = None
        self.pending = {"incoming": [], "outgoing": []}
        self.suggestions = []
        self.actions = []
        self.action_error = None

    def __call__(self, method, path, *, token=None, body=None, user_ip="", user_agent=""):
        self.requests.append((method, path, token))
        if self.error:
            raise self.error
        if path.endswith("/friends/ids"):
            return {"ids": list(self.friends), "total": len(self.friends)}
        if path.startswith("/friends/requests"):
            kind = parse_qs(urlsplit(path).query)["type"][0]
            return {"items": self.pending[kind], "next_cursor": None, "total": len(self.pending[kind])}
        if path.startswith("/friends/suggestions"):
            return {"items": self.suggestions}
        if path.startswith("/friends/"):
            if self.action_error:
                raise self.action_error
            self.actions.append((method, path))
            return {"status": "outgoing", "friends_count": 5, "friend_requests": 1, "extra": "dropped"}
        ids = [int(i) for i in parse_qs(urlsplit(path).query)["ids"][0].split(",")]
        return {"items": [self.people[i] for i in ids if i in self.people]}
