"""
Общее для частей раздела admin_api: курсор с заготовленными ответами, соединение-подделка, приложение
с маршрутами панели и клиент, который подписывает запросы, как панель.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/admin_api: вход (core/tests/test_admin_api.py) собирает части по порядку.
"""
import json
import os
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from core.admin import audit as admin_audit, auth as admin_auth, reports as admin_reports, router as admin_router
from core.admin import settings as admin_settings, summary as admin_summary, titles as admin_titles
from core import core as main

SECRET = "x" * 48
# Части админ-API, которые ходят в базу: подмена connect нужна в каждой
ADMIN_PARTS = (admin_reports, admin_audit, admin_settings, admin_titles, admin_summary)
LOCAL = ("127.0.0.1", 50000)


class ScriptCursor:
    """Отвечает на запрос строками того ответа, чей ключ есть в тексте запроса."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.queries = []
        self.rows = []
        self.lastrowid = 0

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.queries.append((flat, tuple(params)))
        self.rows = next((rows for key, rows in self.answers.items() if key in flat), [])
        if flat.startswith("INSERT INTO aviev_reports"):
            self.lastrowid = 77

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def sql(self, start):
        return [(q, p) for q, p in self.queries if q.startswith(start)]


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


def panel_app():
    app = FastAPI()
    app.include_router(admin_router.router)
    app.add_exception_handler(admin_auth.AdminError, admin_auth.error_response)
    app.add_exception_handler(RequestValidationError, main._validation_error)
    app.add_exception_handler(StarletteHTTPException, main._http_error)
    app.add_exception_handler(Exception, main._server_error)
    return app


class Panel:
    """Клиент, который подписывает запросы, как панель Anime Social."""

    def __init__(self, app, client=LOCAL, raise_errors=True):
        self.http = TestClient(app, client=client, raise_server_exceptions=raise_errors)

    def call(self, method, path, body=None, *, ts=None, user="3", secret=SECRET, headers=None, send=None,
             signed_user=None):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        ts = str(int(time.time()) if ts is None else ts)
        signature = admin_auth.sign(secret.encode(), ts, method, path, signed_user or user or "", raw)
        sent = {"X-AsoPlay-Admin": f"{ts}.{signature}", "Content-Type": "application/json"}
        if user is not None:
            sent["X-AsoPlay-Admin-User"] = user
        sent.update(headers or {})
        return self.http.request(method, path, content=raw if send is None else send, headers=sent)


class PanelCase(unittest.TestCase):
    answers: dict = {}

    def setUp(self):
        self.cursor = ScriptCursor(dict(self.answers))
        self.patches = [
            patch.dict(os.environ, {"AV_ADMIN_SECRET": SECRET}),
            *(patch.object(part, "connect", side_effect=lambda: FakeConn(self.cursor)) for part in ADMIN_PARTS),
            patch.object(admin_auth, "_seen", admin_auth._Seen()),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.panel = Panel(panel_app())


REPORT_ROW = (5, 1535, "Тетрадь смерти", 3, "AniLibria", "anilibria", "kodik", "no_video", "Чёрный экран", 9,
              datetime(2026, 10, 30, 12, 0), "open", "", None, datetime(2026, 10, 30, 12, 0))
