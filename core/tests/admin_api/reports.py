"""
Жалобы «Не работает»: очередь и решения в панели, приём жалоб от зрителей.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/admin_api: вход (core/tests/test_admin_api.py) собирает части по порядку.
"""
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.admin import auth as admin_auth, reports as admin_reports
from core.api.sections import reports as report_routes  # сама проверка называется reports
from core.functions import sessions

from core.tests.admin_api.lib import ScriptCursor, FakeConn, PanelCase, REPORT_ROW


class PanelReportTests(PanelCase):
    answers = {
        "SELECT status, COUNT(*) FROM aviev_reports": [("open", 2), ("fixed", 1)],
        "SELECT status FROM aviev_reports WHERE id": [("open",)],
        "FROM aviev_reports WHERE": [REPORT_ROW],
    }

    def test_the_queue_lists_by_status_with_counts(self):
        data = self.panel.call("GET", "/api/admin/reports?status=open").json()["data"]
        self.assertEqual(data["counts"], {"open": 2, "fixed": 1, "rejected": 0})
        item = data["items"][0]
        self.assertEqual((item["id"], item["title"], item["episode"], item["source_name"], item["player"],
                          item["kind_title"], item["created"]),
                         (5, "Тетрадь смерти", 3, "AniLibria", "kodik", "Видео не запускается", 1793361600))
        self.assertEqual(item["url"], f"{admin_reports.SITE_URL}/anime/1535")
        query, params = self.cursor.sql("SELECT id, mal_id")[0]
        self.assertIn("WHERE status = %s ORDER BY id DESC LIMIT %s", query)
        self.assertEqual(params, ("open", admin_auth.PAGE + 1))
        self.panel.call("GET", "/api/admin/reports?status=open&cursor=40")
        self.assertEqual(self.cursor.sql("SELECT id, mal_id")[-1][1], ("open", 40, admin_auth.PAGE + 1))
        self.assertEqual(self.panel.call("GET", "/api/admin/reports?status=all").status_code, 422)

    def test_a_decision_is_saved_with_the_audit(self):
        resp = self.panel.call("PATCH", "/api/admin/reports/5", {"status": "fixed", "note": "Поменяли\nисточник"})
        self.assertEqual(resp.status_code, 200)
        update = self.cursor.sql("UPDATE aviev_reports")[0][1]
        self.assertEqual(update[:3], ("fixed", "Поменяли источник", 3))
        audit = self.cursor.sql("INSERT INTO aviev_admin_audit")[0][1]
        self.assertEqual(audit[:3], (3, "report.fixed", "report:5"))                 # как называет панель
        self.assertEqual(json.loads(audit[3]), {"from": "open", "note": "Поменяли источник"})

    def test_an_unknown_report_or_status_is_refused(self):
        self.cursor.answers["SELECT status FROM aviev_reports WHERE id"] = []
        self.assertEqual(self.panel.call("PATCH", "/api/admin/reports/6", {"status": "fixed"}).status_code, 404)
        self.assertEqual(self.panel.call("PATCH", "/api/admin/reports/5", {"status": "done"}).status_code, 422)


class PublicReportTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(report_routes.router)
        app.dependency_overrides[sessions.current_user_required] = lambda: {"id": 9}
        self.client = TestClient(app)
        self.cursor = ScriptCursor({"FROM aviev_title_pages": [("Тетрадь смерти", "Death Note")]})
        for p in (patch.object(report_routes, "connect", side_effect=lambda: FakeConn(self.cursor)),
                  patch.object(report_routes, "_limiter", report_routes.rate_limit.SlidingWindow(2, 3600))):
            p.start()
            self.addCleanup(p.stop)

    def report(self, **extra):
        body = {"mal_id": 1535, "episode": 3, "dub": "AniLibria\n", "source": "AniLibria", "player": "KodikPlayer.com",
                "kind": "no_video", "text": "Чёрный экран"} | extra
        return self.client.post("/reports", json=body)

    def test_reasons_follow_the_dialog_order(self):
        kinds = self.client.get("/reports/kinds").json()
        self.assertEqual(kinds["kinds"][0], {"code": "no_video", "title": "Видео не запускается"})
        self.assertEqual(kinds["kinds"][-1]["code"], "other")
        self.assertEqual(kinds["text_max"], 300)

    def test_a_report_keeps_what_the_player_showed(self):
        self.assertEqual(self.report().json(), {"ok": True, "id": 77, "repeated": False})
        params = self.cursor.sql("INSERT INTO aviev_reports")[0][1]
        self.assertEqual(params[:9], (9, 1535, "Тетрадь смерти", 3, "AniLibria", "anilibria", "kodikplayer.com", "no_video", "Чёрный экран"))

    def test_bad_reasons_and_sources_are_refused(self):
        self.assertEqual(self.report(kind="spam").status_code, 422)
        self.assertEqual(self.report(source="../etc").status_code, 422)
        self.assertEqual(self.report(player="<b>").status_code, 422)

    def test_the_same_open_report_within_a_day_is_not_repeated(self):
        self.cursor.answers = {"SELECT id FROM aviev_reports": [(41,)]}
        self.assertEqual(self.report(text="Теперь и звука нет").json(), {"ok": True, "id": 41, "repeated": True})
        self.assertEqual(self.cursor.sql("UPDATE aviev_reports")[0][1][:2], ("Теперь и звука нет", "AniLibria"))
        self.assertEqual(self.cursor.sql("INSERT"), [])

    def test_too_many_reports_an_hour_are_refused(self):
        self.report()
        self.report(episode=4)
        resp = self.report(episode=5)
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(int(resp.headers["retry-after"]) > 0)

    def test_guests_cannot_report(self):
        app = FastAPI()
        app.include_router(report_routes.router)
        self.assertEqual(TestClient(app).post("/reports", json={"mal_id": 1, "kind": "other"}).status_code, 401)
