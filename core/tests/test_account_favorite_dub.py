"""
Любимая озвучка в настройках аккаунта (GET и PUT /account/settings, поле favorite_dub): хранится в аккаунте, меняется
только когда её прислали, сбрасывается пустой строкой, чужой текст приходит очищенным. Без базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from core.api.sections.account import settings as settings_section
from core.tests.as_events.lib import FakeConn

USER = {"id": 999999999}  # такого человека нет: проверка без базы


class GetTests(unittest.TestCase):
    def get(self, rows):
        with patch.object(settings_section, "connect", return_value=FakeConn(lambda sql, params: (rows, len(rows)))):
            return settings_section.settings_get(USER)

    def test_without_a_row_nothing_is_chosen(self):
        self.assertEqual(self.get([])["favorite_dub"], "")

    def test_the_saved_favourite_comes_back_with_the_other_settings(self):
        got = self.get([(1, 0, None, "AniLibria")])
        self.assertEqual(got, {"autonext": True, "auto_add_lists": False, "extra": {}, "favorite_dub": "AniLibria"})


class PutTests(unittest.TestCase):
    def put(self, **fields):
        conn = FakeConn()
        with patch.object(settings_section, "connect", return_value=conn):
            settings_section.settings_put(settings_section.SettingsIn(**fields), USER)
        (sql, params), = conn.statements("INSERT INTO aviev_account_settings")
        return sql, params

    def test_a_favourite_is_stored_on_insert_and_on_update(self):
        sql, params = self.put(favorite_dub="  Studio   Band ")
        self.assertIn("favorite_dub=%s", sql)
        self.assertEqual(params[4], "Studio Band")  # новая строка
        self.assertIn("Studio Band", params[6:])  # и обновление существующей

    def test_other_settings_leave_the_favourite_alone(self):
        sql, params = self.put(autonext=False)
        self.assertNotIn("favorite_dub=%s", sql)
        self.assertEqual(params[4], "")  # у новой строки любимой ещё нет

    def test_an_empty_name_resets_the_favourite(self):
        sql, params = self.put(favorite_dub="")
        self.assertIn("favorite_dub=%s", sql)
        self.assertEqual(params[6], "")

    def test_foreign_text_is_cleaned_and_capped(self):
        _, params = self.put(favorite_dub="JAM\x00‮" + "я" * 300)
        self.assertEqual(params[6], ("JAM " + "я" * 300)[:200])

    def test_an_overlong_value_is_refused(self):
        with self.assertRaises(ValidationError):
            settings_section.SettingsIn(favorite_dub="я" * 1001)


if __name__ == "__main__":
    unittest.main()
