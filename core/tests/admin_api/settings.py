"""
Настройки сайта из панели: источники, способы входа, объявление, хосты картинок через прокси; сохраняется только
изменённое, вкладки узнают сразу.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/admin_api: вход (core/tests/test_admin_api.py) собирает части по порядку.
"""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from core.admin import settings as admin_settings
from core.admin.audit import DETAILS_MAX
from core.classes import as_sessions
from core.classes.vpn import bridge as vpn_bridge
from core.config import config
from core.functions import public_suffix, site_settings

from core.tests.admin_api.lib import PanelCase


class SettingsTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.announced = []
        # Общие суффиксы — маленький список вместо файла publicsuffix.org
        suffixes = Path(tempfile.mkdtemp(prefix="asoplay-psl-")) / "public_suffix_list.dat"
        suffixes.write_text("\n".join(("com", "org", "uk", "co.uk", "io", "github.io")), encoding="utf-8")
        listed = patch.object(config, "PUBLIC_SUFFIX_FILE", suffixes)
        listed.start()
        self.addCleanup(listed.stop)
        public_suffix._rules.cache_clear()
        self.addCleanup(public_suffix._rules.cache_clear)
        p = patch.object(site_settings, "listeners", [self.announced.append])
        p.start()
        self.addCleanup(p.stop)
        state = patch.object(site_settings, "state", site_settings._State())
        state.start()
        self.addCleanup(state.stop)
        self.stored = {}

        def load():
            current = site_settings.Settings()
            site_settings.state.settings = site_settings.Settings(
                notice=self.stored.get("notice", current.notice),
                features=self.stored.get("features", current.features),
                sources=tuple(self.stored.get("sources", ())),
                proxy_hosts=tuple(self.stored.get("proxy_hosts", ())),
            )
        site_settings.state.load = load
        original = self.cursor.execute

        def execute(sql, params=()):
            original(sql, params)
            if "INSERT INTO aviev_site_settings" in sql:
                self.stored[params[0]] = json.loads(params[1])
        self.cursor.execute = execute

    def test_all_sources_are_shown_in_order_with_what_can_be_switched_on(self):
        with patch.object(as_sessions, "enabled", return_value=False), patch.object(vpn_bridge, "is_active", return_value=True):
            data = self.panel.call("GET", "/api/admin/settings").json()["data"]
        self.assertEqual([s["id"] for s in data["sources"]][:3], ["yummy_anime", "oldyummy", "animego"])
        self.assertEqual(data["sources"][0], {"id": "yummy_anime", "name": "YummyAnime", "enabled": True, "order": 1})
        self.assertEqual(data["available"], {"as_login": False, "password_login": True, "comments": False, "vpn": True})
        self.assertEqual(data["notice"], {"text": "", "until": None})
        self.assertEqual(data["proxy_hosts"], [])

    def test_the_proxy_list_is_stored_in_its_compared_form_and_audited_as_a_diff(self):
        resp = self.panel.call("PUT", "/api/admin/settings", {"proxy_hosts": [
            {"host": " Пример.РФ. ", "via": "vpn"}, {"host": "img.example.com"}, {"host": "xn--e1afmkfd.xn--p1ai"}]})
        self.assertEqual(resp.status_code, 200, resp.text)
        listed = [{"host": "xn--e1afmkfd.xn--p1ai", "via": "vpn"}, {"host": "img.example.com", "via": "site"}]
        self.assertEqual(resp.json()["data"]["proxy_hosts"], listed)                  # повтор схлопнут, первый остался
        self.assertEqual(site_settings.public_state()["proxy_hosts"], ["xn--e1afmkfd.xn--p1ai", "img.example.com"])
        self.assertEqual(self.announced, [{"proxy_hosts"}])
        self.panel.call("PUT", "/api/admin/settings", {"proxy_hosts": [
            {"host": "img.example.com", "via": "vpn"}, {"host": "new.example.org"}]})
        details = [json.loads(params[3]) for _, params in self.cursor.sql("INSERT INTO aviev_admin_audit")]
        self.assertEqual(details, [
            {"added": listed, "removed": [], "changed": [], "omitted": 0},
            {"added": [{"host": "new.example.org", "via": "site"}], "removed": [listed[0]],
             "changed": [{"host": "img.example.com", "via": "vpn", "was": "site"}], "omitted": 0},
        ])
        self.assertEqual(site_settings.proxy_via("cdn.img.example.com"), "vpn")

    def test_a_wrong_host_is_refused_with_its_own_text(self):
        for bad in ("https://img.example.com", "img.example.com/x", "img.example.com:8080", "127.0.0.1", "[::1]",
                    "0x7f.1", "2130706433", "a.0x7f", "localhost", "a..example.com", "-a.example.com", "user@a.com"):
            resp = self.panel.call("PUT", "/api/admin/settings",
                                   {"proxy_hosts": [{"host": "ok.example.com"}, {"host": bad}]})
            self.assertEqual((resp.status_code, resp.json()["error"]),
                             (422, {"code": "invalid_host", "message": f"Неверный хост: {bad}"}), bad)
        resp = self.panel.call("PUT", "/api/admin/settings", {"proxy_hosts": [{"host": "GitHub.io"}]})
        self.assertEqual((resp.status_code, resp.json()["error"]["message"]),
                         (422, "Неверный хост: GitHub.io — это общий суффикс: под ним сайты разных владельцев, "
                               "укажите сам сайт"))
        self.assertEqual(self.stored, {})

    def test_a_long_diff_still_fits_the_audit_record(self):
        hosts = [{"host": f"img{n:03}.a-rather-long-image-host-name.example", "via": "site"} for n in range(100)]
        details = admin_settings._proxy_hosts_diff([], hosts)
        self.assertLessEqual(len(json.dumps(details, ensure_ascii=False)), DETAILS_MAX)
        self.assertEqual((len(details["added"]) + details["omitted"], details["removed"], details["changed"]), (100, [], []))
        self.assertGreater(details["omitted"], 0)

    def test_both_ways_to_sign_in_cannot_be_switched_off(self):
        self.panel.call("PUT", "/api/admin/settings", {"features": {"as_login": False}})
        resp = self.panel.call("PUT", "/api/admin/settings", {"features": {"password_login": False}})
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (422, "no_sign_in_left"))

    def test_an_unknown_source_is_refused(self):
        resp = self.panel.call("PUT", "/api/admin/settings", {"sources": [{"id": "animevost", "enabled": True, "order": 0}]})
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (422, "unknown_source"))

    def test_a_change_is_stored_audited_and_announced_to_the_tabs(self):
        until = int(time.time()) + 3600
        resp = self.panel.call("PUT", "/api/admin/settings", {
            "notice": {"text": "Сегодня  ночью\nработы", "until": until},
            "sources": [{"id": "anilibria", "enabled": True, "order": 0}, {"id": "yummy_anime", "enabled": False, "order": 1}],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["notice"], {"text": "Сегодня ночью работы", "until": until})
        self.assertEqual(site_settings.source_order()[:2], ["anilibria", "oldyummy"])
        self.assertFalse(site_settings.source_enabled("yummy_anime"))
        self.assertEqual(site_settings.notice(), {"text": "Сегодня ночью работы", "until": until})
        self.assertEqual(self.announced, [{"notice", "sources"}])
        audit = [params[1:3] for _, params in self.cursor.sql("INSERT INTO aviev_admin_audit")]
        self.assertEqual(audit, [("settings.notice", "settings:notice"), ("settings.sources", "settings:sources")])

    def test_the_whole_object_from_the_panel_saves_only_what_changed(self):
        view = self.panel.call("GET", "/api/admin/settings").json()["data"]
        whole = {"notice": view["notice"], "features": dict(view["features"], as_login=False),
                 "sources": [{k: s[k] for k in ("id", "enabled", "order")} for s in view["sources"]]}
        self.assertEqual(self.panel.call("PUT", "/api/admin/settings", whole).status_code, 200)
        self.assertEqual(set(self.stored), {"features"})
        self.assertEqual([params[1] for _, params in self.cursor.sql("INSERT INTO aviev_admin_audit")], ["settings.features"])
        self.assertEqual(self.announced, [{"features"}])
        self.cursor.queries.clear()
        self.assertEqual(self.panel.call("PUT", "/api/admin/settings", whole, ts=int(time.time()) + 1).status_code, 200)  # то же позже
        self.assertEqual(self.cursor.queries, [])
        self.assertEqual(self.announced, [{"features"}])
