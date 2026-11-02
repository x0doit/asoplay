"""
Закрытое не выходит наружу: открытый профиль — без почты и логина (запасное имя — handle), ошибки базы и схемы —
только в журнал, а наружу 503 и 500 без подробностей; /health — без текста сбоя; страница тайтла при недоступном
каталоге — оболочка без текста. Без сети и без базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from core.api.sections import profile as profile_routes
from core.classes import accounts, db
from core.modules import profile_pages, title_cache, title_pages
from core.tests.as_events.lib import FakeConn

SCHEMA = {"user_table": "Just_accounts", "id_col": "user_id", "login_col": "username", "email_col": "email",
          "name_col": "name", "status_col": "banned", "avatar_col": "photo", "handle_col": "alias"}
SECRET = "Access denied for user 'secret_db_user'@'localhost' (using password: YES)"
HIDDEN = {"hide_lists": True, "hide_activity": True}


class Conn(FakeConn):
    def close(self):
        pass


class PublicProfile(unittest.TestCase):
    def resolve(self, handle, row, schema=SCHEMA):
        conn = Conn(lambda sql, params: ([row] if sql.startswith("SELECT") else [], 1))
        with patch.object(accounts, "introspect_schema", return_value=dict(schema)), \
                patch.object(accounts, "connect", return_value=conn):
            return accounts.resolve_handle(handle), conn

    def test_neither_email_nor_login_is_selected_and_an_empty_name_becomes_the_handle(self):
        user, conn = self.resolve("profile7", (7, None, None, None, 0))
        (sql, _), = conn.sql
        self.assertNotIn("`email`", sql)
        self.assertNotIn("`username`", sql)
        self.assertEqual(user["name"], "profile7")
        self.assertFalse({"email", "login"} & set(user))

    def test_an_alias_is_the_fallback_name(self):
        user, _ = self.resolve("sakura", (7, "", None, "sakura", 0))
        self.assertEqual((user["name"], user["handle"]), ("sakura", "sakura"))

    def test_a_name_found_in_the_login_column_is_not_used(self):
        user, conn = self.resolve("profile7", (7, None, None, 0), dict(SCHEMA, name_col="username"))
        self.assertNotIn("`username`", conn.sql[0][0])
        self.assertEqual(user["name"], "profile7")

    def test_the_summary_and_the_page_show_the_handle_and_no_email(self):
        conn = Conn(lambda sql, params: ([(7, None, None, None, 0)] if sql.startswith("SELECT") else [], 1))
        with patch.object(accounts, "introspect_schema", return_value=dict(SCHEMA)), \
                patch.object(accounts, "connect", return_value=conn), \
                patch.object(profile_routes, "read_privacy", return_value=HIDDEN), \
                patch.object(profile_pages, "read_privacy", return_value=HIDDEN):
            summary = profile_routes.profile_summary("profile7", days=30, viewer=None)
            page = profile_pages.profile_page("profile7").body.decode()
        self.assertEqual(summary["user"]["name"], "profile7")
        self.assertIn("<h1>profile7</h1>", page)
        self.assertTrue(all("`email`" not in sql and "`username`" not in sql for sql, _ in conn.sql))
        self.assertNotIn("@example", json.dumps(summary) + page)


class DatabaseErrors(unittest.TestCase):
    def setUp(self):
        def refuse(**_):
            raise RuntimeError(SECRET)
        fake = type("FakePyMySQL", (), {"connect": staticmethod(refuse)})
        for p in (patch.object(db, "_pymysql", fake),
                  patch.object(db, "db_config", return_value={"host": "h", "user": "secret_db_user", "password": "x",
                                                               "database": "d", "port": 3306})):
            p.start()
            self.addCleanup(p.stop)

    def test_a_failed_connect_says_only_unavailable_and_logs_the_reason(self):
        with self.assertLogs("asoplay.db", "WARNING") as logs, self.assertRaises(HTTPException) as error:
            with db.connect():
                pass
        self.assertEqual((error.exception.status_code, error.exception.detail), (503, "database unavailable"))
        self.assertTrue(any("secret_db_user" in line for line in logs.output))

    def test_health_hides_the_reason_of_a_failed_connect_and_of_a_failed_query(self):
        with self.assertLogs("asoplay.db", "WARNING"):
            self.assertEqual(db.health(), {"ok": False, "error": "unavailable"})

        def broken(sql, params):
            raise RuntimeError(SECRET)
        with patch.object(db._pymysql, "connect", staticmethod(lambda **_: Conn(broken))), \
                self.assertLogs("asoplay.db", "WARNING") as logs:
            self.assertEqual(db.health(), {"ok": False, "error": "unavailable"})
        self.assertTrue(any("secret_db_user" in line for line in logs.output))


class SchemaErrors(unittest.TestCase):
    def test_schema_errors_name_no_table_or_column_outside(self):
        def answer(sql, params):
            if sql == "SHOW TABLES":
                return [("Just_accounts",)], 1
            return ([("nothing",)] if sql.startswith("DESCRIBE") else []), 1
        with patch.object(accounts, "_schema_cache", None), \
                patch.object(accounts, "connect", return_value=Conn(answer)), \
                patch.object(accounts, "db_config", return_value={"prefix": "Just"}), \
                patch.object(accounts.config, "user_table", return_value=None), \
                patch.object(accounts.config, "user_column", return_value=None), \
                self.assertLogs("asoplay.auth", "ERROR") as logs, self.assertRaises(HTTPException) as error:
            accounts.introspect_schema()
        self.assertEqual((error.exception.status_code, error.exception.detail), (500, "accounts schema unavailable"))
        self.assertTrue(any("Just_accounts" in line for line in logs.output))


class TitlePage(unittest.IsolatedAsyncioTestCase):
    async def test_an_unreachable_catalog_or_database_gives_the_shell_without_the_reason(self):
        for cached, fetched in ((None, HTTPException(503, SECRET)), (HTTPException(503, SECRET), None)):
            with self.subTest(database_down=fetched is None), \
                    patch.object(title_cache, "load_cached", side_effect=[cached] if cached is None else cached), \
                    patch.object(title_cache, "fetch_and_store", side_effect=fetched), \
                    patch.object(title_pages.site_settings, "hidden", return_value=False):
                page = await title_pages.canonical_title("1535")
                self.assertEqual((page.status_code, page.headers["X-Robots-Tag"]), (503, "noindex"))
                self.assertNotIn("secret_db_user", page.body.decode())


if __name__ == "__main__":
    unittest.main()
