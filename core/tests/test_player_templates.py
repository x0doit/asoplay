"""
Тексты кадров плеера из design/EDDAS/player: шапка с автором срезается ровно одна, без неё или с двумя — ошибка при
запуске, подстановка — за один проход, шаблоны собираются с ровно своими местами.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Шапка-комментарий нужна читателю файла, в ответ она не идёт. Загрузчик (core/modules/player/routes.py, _text) читает
файлы при импорте модуля, поэтому сломанный файл останавливает запуск, а не уходит в кадр лишним текстом.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.modules.player import routes as player

AUTHOR = "Автор: Чепела Даниил Максимович."
JS_HEADER = f"/*\n * Часть кадра.\n *\n * {AUTHOR}\n */\n"
HTML_HEADER = f"<!--\n  Страница кадра.\n\n  {AUTHOR}\n-->\n"


class Loader(unittest.TestCase):
    def load(self, name: str, text: str) -> str:
        """_text для файла с этим содержимым во временной папке кадров."""
        folder = Path(tempfile.mkdtemp())
        (folder / name).write_bytes(text.encode("utf-8"))
        with patch.object(player.config, "PLAYER_DIR", folder):
            return player._text(name)

    def test_one_header_is_cut_exactly(self):
        self.assertEqual(self.load("a.js", JS_HEADER + "const a = 1; /* не шапка */\n"), "const a = 1; /* не шапка */\n")
        self.assertEqual(self.load("a.html", HTML_HEADER + "<p>@@message@@</p>"), "<p>@@message@@</p>")

    def test_line_endings_of_a_windows_checkout_do_not_matter(self):
        self.assertEqual(self.load("a.js", (JS_HEADER + "x();\n").replace("\n", "\r\n")), "x();\n")

    def test_a_broken_file_stops_the_start(self):
        broken = {
            "без шапки": "const a = 1;\n",
            "шапка не в начале": "\n" + JS_HEADER + "x();\n",
            "без автора": "/*\n * Часть кадра.\n */\nx();\n",
            "две шапки": JS_HEADER + JS_HEADER + "x();\n",
            "автор ещё и в тексте": JS_HEADER + f"// {AUTHOR}\nx();\n",
            "шапка не закрыта": f"/*\n * {AUTHOR}\nx();\n",
            "html без автора": "<!--\n  Страница.\n-->\n<p></p>",
        }
        for label, text in broken.items():
            with self.assertRaises(RuntimeError, msg=label):
                self.load("a.js", text)

    def test_fill_is_one_pass(self):
        # Вставленное значение повторно не просматривается — как у f-string, которой был кадр
        self.assertEqual(player._fill("@@a@@|@@b@@", a="@@b@@", b="x"), "@@b@@|x")

    def test_a_template_must_have_exactly_its_slots(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "t.html").write_bytes((HTML_HEADER + "<p>@@one@@ @@part@@</p>").encode("utf-8"))
        (folder / "p.js").write_bytes((JS_HEADER + "@@two@@").encode("utf-8"))
        with patch.object(player.config, "PLAYER_DIR", folder):
            self.assertEqual(player._template("t.html", {"one", "two"}, part=("p.js",)), "<p>@@one@@ @@two@@</p>")
            with self.assertRaises(RuntimeError):
                player._template("t.html", {"one"}, part=("p.js",))

    def test_served_templates_carry_no_header(self):
        for text in (player._FRAME_PAGE, player._BRIDGE_PAGE, player._ERROR_PAGE, player._SHIELD_LOGGER_JS):
            self.assertNotIn(AUTHOR, text)
            self.assertNotIn("design/EDDAS/player", text)


if __name__ == "__main__":
    unittest.main()
