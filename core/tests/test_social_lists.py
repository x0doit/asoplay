"""
Блок «Аниме» для соцсети (clients.md, раздел 17): подпись запроса — верная, чужая, админская, просроченная, через
прокси, с чужого адреса, без секрета — и ответ: скрытые списки, пустые, больше 20 в статусе, адреса только свои,
название без HTML, серия только у «Смотрю». Без базы: ответы подменены.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import hashlib
import hmac
import os
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from core import core as main
from core.config.config import SITE_URL
from core.functions import social_lists
from core.tests.as_events.lib import FakeConn

SECRET = "x" * 48
LOCAL = ("127.0.0.1", 50000)
PATH = "/api/social/lists/20"
AT = datetime(2026, 11, 10, 12, 0, 0)


def signed(ts=None, secret=SECRET, prefix="social\n", path=PATH):
    """Заголовок подписи соцсети для GET path."""
    ts = str(int(time.time()) if ts is None else ts)
    message = f"{prefix}{ts}\nGET\n{path}\n".encode("utf-8")
    return {"X-AsoPlay-Social": f"{ts}.{hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()}"}


class SignatureTests(unittest.TestCase):
    def setUp(self):
        for item in (patch.dict(os.environ, {"AV_ADMIN_SECRET": SECRET}),
                     patch.object(social_lists, "block", return_value={"hidden": True, "settings_url": "s"})):
            item.start()
            self.addCleanup(item.stop)
        self.client = TestClient(main.app, client=LOCAL)

    def error(self, resp):
        return resp.status_code, resp.json()["error"]["code"]

    def test_a_signed_direct_request_gets_the_block(self):
        resp = self.client.get(PATH, headers=signed())
        self.assertEqual((resp.status_code, resp.json()), (200, {"ok": True, "data": {"hidden": True, "settings_url": "s"}}))

    def test_a_foreign_or_admin_signature_is_refused(self):
        self.assertEqual(self.error(self.client.get(PATH, headers=signed(secret="y" * 48))), (401, "bad_signature"))
        self.assertEqual(self.error(self.client.get(PATH, headers=signed(prefix=""))), (401, "bad_signature"))
        self.assertEqual(self.error(self.client.get(PATH)), (401, "bad_signature"))

    def test_an_old_signature_is_expired(self):
        self.assertEqual(self.error(self.client.get(PATH, headers=signed(ts=int(time.time()) - 120))), (401, "expired"))

    def test_through_a_proxy_or_from_another_address_there_is_no_such_page(self):
        for extra in ({"X-Forwarded-For": "203.0.113.5"}, {"X-Real-IP": "203.0.113.5"}):
            self.assertEqual(self.error(self.client.get(PATH, headers={**signed(), **extra})), (404, "not_found"))
        outside = TestClient(main.app, client=("203.0.113.5", 50000))
        self.assertEqual(self.error(outside.get(PATH, headers=signed())), (404, "not_found"))

    def test_without_the_secret_the_page_is_not_configured_and_a_bad_id_is_a_validation_error(self):
        with patch.dict(os.environ, {"AV_ADMIN_SECRET": ""}):
            self.assertEqual(self.error(self.client.get(PATH, headers=signed())), (503, "not_configured"))
        bad = "/api/social/lists/0"
        self.assertEqual(self.error(self.client.get(bad, headers=signed(path=bad))), (422, "validation_failed"))


def answer(counts, rows, episodes=(), names=()):
    """Ответы базы на запросы блока: числа, записи статуса (по параметру), серии «Смотрю», названия страниц."""
    def reply(sql, params):
        if "COUNT(*)" in sql:
            return list(counts), 1
        if "FROM aviev_user_lists" in sql:
            return list(rows.get(params[1], [])), 1
        if "aviev_watch_history" in sql:
            return list(episodes), 1
        if "aviev_title_pages" in sql:
            return list(names), 1
        return [], 1
    return reply


class BlockTests(unittest.TestCase):
    def build(self, conn, hidden=False):
        with patch.object(social_lists, "read_privacy", return_value={"hide_lists": hidden}), \
                patch.object(social_lists, "connect", return_value=conn), \
                patch.object(social_lists, "resolve_handle", return_value={"handle": "sakura_chan"}), \
                patch.object(social_lists.site_settings, "img_hosts", return_value=[]), \
                patch.object(social_lists.image_hosts, "url_refusal",
                             side_effect=lambda url, hosts: None if "cdn.myanimelist.net" in url else "host"):
            return social_lists.block(20)

    def test_hidden_lists_give_only_the_settings_link(self):
        self.assertEqual(self.build(FakeConn(), hidden=True), {"hidden": True, "settings_url": f"{SITE_URL}/my/settings"})

    def test_no_lists_are_zeros_and_empty_items_not_an_error(self):
        data = self.build(FakeConn(answer([], {})))
        self.assertEqual(data["counts"], {"watching": 0, "planned": 0, "completed": 0})
        self.assertEqual(data["items"], {"watching": [], "planned": [], "completed": []})
        self.assertEqual((data["hidden"], data["updated_at"], data["lists_url"]),
                         (False, None, f"{SITE_URL}/@sakura_chan/lists"))

    def test_a_full_block_has_own_addresses_plain_titles_and_episodes_only_for_watching(self):
        poster = "https://cdn.myanimelist.net/images/anime/1/1.jpg"
        watching = [(52991, "<b>Фрирен</b> &amp; друзья", poster, AT)] + [(i, "Т" * 300, "", AT) for i in range(1, 20)]
        rows = {"watching": watching, "planned": [(7, "", "https://evil.example/x.jpg", datetime(2026, 11, 9))],
                "completed": [(52991, "Фрирен", poster, AT)]}
        conn = FakeConn(answer([("watching", 25), ("completed", 1)], rows, episodes=[(52991, 6)], names=[(7, "Семь", "Seven")]))
        data = self.build(conn)
        self.assertEqual(data["counts"], {"watching": 25, "planned": 0, "completed": 1})  # числа — полные
        self.assertEqual(len(data["items"]["watching"]), 20)
        limits = [params[-1] for sql, params in conn.sql if "ORDER BY updated_at DESC" in sql]
        self.assertEqual(limits, [20, 20, 20])
        first = data["items"]["watching"][0]
        self.assertEqual(first, {"mal_id": 52991, "title": "Фрирен & друзья",
                                 "poster_url": f"{SITE_URL}/proxy/img?url=https%3A%2F%2Fcdn.myanimelist.net%2Fimages%2Fanime%2F1%2F1.jpg",
                                 "url": f"{SITE_URL}/anime/52991", "episode": 6, "updated_at": 1794312000})
        self.assertEqual(len(data["items"]["watching"][1]["title"]), 200)
        planned = data["items"]["planned"][0]
        self.assertEqual((planned["title"], planned["poster_url"], planned["episode"]), ("Семь", None, None))
        self.assertIsNone(data["items"]["completed"][0]["episode"])
        urls = [data["lists_url"]] + [item[key] for found in data["items"].values() for item in found
                                      for key in ("url", "poster_url") if item[key]]
        self.assertTrue(all(url.startswith(SITE_URL + "/") for url in urls))
        self.assertEqual(data["updated_at"], 1794312000)


class ListsPageTests(unittest.TestCase):
    def test_the_lists_link_opens_the_profile_page_which_scrolls_to_the_lists(self):
        person = {"id": 20, "handle": "sakura_chan", "name": "Сакура", "avatar": ""}
        with patch.object(main.profile_pages, "resolve_handle", side_effect=lambda h: person if h == "sakura_chan" else None), \
                patch.object(main.profile_pages.social_pages, "closed", return_value=False), \
                patch.object(main.profile_pages, "read_privacy", return_value={"hide_lists": False, "hide_activity": False}), \
                patch.object(main.profile_pages.lists, "counts_for", return_value={}):
            client = TestClient(main.app)
            page = client.get("/@sakura_chan/lists")
            missing = client.get("/@nobody/lists")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f'<link rel="canonical" href="{SITE_URL}/@sakura_chan">', page.text)  # одна страница — один адрес
        self.assertEqual(missing.status_code, 404)
        app_js = (main.env.ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("m = path.match(/^\\/@([a-zA-Z0-9_.-]+)(\\/lists)?\\/?$/);", app_js)
        self.assertIn("account.viewProfile(m[1], { lists: !!m[2] })", app_js)


if __name__ == "__main__":
    unittest.main()
