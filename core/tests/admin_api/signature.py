"""
Подпись запросов панели: секрет, время, повторы, имя админа, тело и строка запроса. Через публичный
прокси и снаружи панели нет; ошибки — в конверте API v2.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/admin_api: вход (core/tests/test_admin_api.py) собирает части по порядку.
"""
import os
import time
from unittest.mock import patch
from urllib.parse import quote

from core.admin import audit as admin_audit, auth as admin_auth
from core.admin import titles as admin_titles
from core.classes.jikan import catalog as jikan_catalog

from core.tests.admin_api.lib import SECRET, panel_app, Panel, PanelCase, REPORT_ROW


class SignatureTests(PanelCase):
    answers = {"FROM aviev_admin_audit": [], "SELECT status FROM aviev_reports WHERE id": [("open",)],
               "FROM aviev_reports WHERE": [REPORT_ROW]}

    def error(self, resp):
        return resp.status_code, resp.json()["error"]["code"]

    def test_a_signed_request_passes(self):
        resp = self.panel.call("GET", "/api/admin/audit")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "data": {"items": [], "next_cursor": None}})

    def test_missing_wrong_old_and_repeated_signatures_are_refused(self):
        self.assertEqual(self.error(self.panel.http.get("/api/admin/audit")), (401, "bad_signature"))
        self.assertEqual(self.error(self.panel.call("GET", "/api/admin/audit", secret="y" * 48)), (401, "bad_signature"))
        self.assertEqual(self.error(self.panel.call("GET", "/api/admin/audit", ts=int(time.time()) - 61)), (401, "expired"))
        ts = int(time.time())
        self.assertEqual(self.panel.call("GET", "/api/admin/audit", ts=ts).status_code, 200)
        self.assertEqual(self.panel.call("GET", "/api/admin/audit", ts=ts).status_code, 200)   # чтение — можно
        change = {"status": "fixed"}
        self.assertEqual(self.panel.call("PATCH", "/api/admin/reports/5", change, ts=ts).status_code, 200)
        self.assertEqual(self.error(self.panel.call("PATCH", "/api/admin/reports/5", change, ts=ts)), (401, "replayed"))
        self.assertEqual(self.panel.call("PATCH", "/api/admin/reports/5", change, ts=ts, user="4").status_code, 200)

    def test_the_admin_in_the_journal_cannot_be_swapped(self):
        resp = self.panel.call("PATCH", "/api/admin/reports/5", {"status": "fixed"}, user="4", signed_user="3")
        self.assertEqual(self.error(resp), (401, "bad_signature"))

    def test_the_body_and_the_query_are_signed(self):
        resp = self.panel.call("PATCH", "/api/admin/reports/5", {"status": "fixed"}, send=b'{"status": "rejected"}')
        self.assertEqual(self.error(resp), (401, "bad_signature"))
        ts = str(int(time.time()))
        signature = admin_auth.sign(SECRET.encode(), ts, "GET", "/api/admin/reports?status=open", "3", b"")
        resp = self.panel.http.get("/api/admin/reports?status=fixed",
                                   headers={"X-AsoPlay-Admin": f"{ts}.{signature}", "X-AsoPlay-Admin-User": "3"})
        self.assertEqual(self.error(resp), (401, "bad_signature"))

    def test_an_encoded_query_is_signed_as_sent(self):
        path = "/api/admin/titles?q=" + quote("Тетрадь")
        with patch.object(admin_titles, "_cached_matches", return_value={1535: "Тетрадь смерти"}), \
                patch.object(jikan_catalog, "search_titles", side_effect=AssertionError("found enough")), \
                patch.object(admin_titles, "_flags_for", return_value={}):
            admin_auth.PAGE, page = 1, admin_auth.PAGE
            try:
                resp = self.panel.call("GET", path)
            finally:
                admin_auth.PAGE = page
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["items"][0]["title"], "Тетрадь смерти")

    def test_through_the_public_proxy_or_from_outside_it_does_not_exist(self):
        resp = self.panel.call("GET", "/api/admin/audit", headers={"X-Forwarded-For": "203.0.113.5"})
        self.assertEqual(self.error(resp), (404, "not_found"))
        resp = self.panel.call("GET", "/api/admin/audit", headers={"X-Real-IP": "203.0.113.5"})
        self.assertEqual(self.error(resp), (404, "not_found"))
        outside = Panel(panel_app(), client=("203.0.113.5", 50000))
        self.assertEqual(self.error(outside.call("GET", "/api/admin/audit")), (404, "not_found"))

    def test_without_a_secret_it_is_switched_off(self):
        with patch.dict(os.environ, {"AV_ADMIN_SECRET": ""}):
            self.assertEqual(self.error(self.panel.call("GET", "/api/admin/audit")), (503, "not_configured"))

    def test_the_admin_is_named(self):
        self.assertEqual(self.error(self.panel.call("GET", "/api/admin/audit", user=None)), (400, "bad_admin_user"))
        self.assertEqual(self.error(self.panel.call("GET", "/api/admin/audit", user="0")), (400, "bad_admin_user"))

    def test_validation_errors_come_in_the_api_v2_envelope(self):
        resp = self.panel.call("PATCH", "/api/admin/reports/5", {"status": 5})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json(), {"ok": False, "error": {"code": "validation_failed", "message": "Неверное поле: status"}})

    def test_unknown_methods_and_failures_answer_in_the_envelope_too(self):
        resp = self.panel.call("GET", "/api/admin/nothing")
        self.assertEqual((resp.status_code, resp.json()), (404, {"ok": False, "error": {"code": "not_found", "message": "Нет такого метода"}}))
        broken = Panel(panel_app(), raise_errors=False)
        with patch.object(admin_audit, "connect", side_effect=RuntimeError("db is down")):
            resp = broken.call("GET", "/api/admin/audit")
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (500, "internal_error"))
        self.assertNotIn("db is down", resp.text)
