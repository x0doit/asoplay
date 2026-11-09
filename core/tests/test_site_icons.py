"""
Значок сайта: ссылки в шапке index.html (её же берут SSR-страницы), manifest.webmanifest с именем, цветом и значками
для телефона и его маршрут с типом application/manifest+json. Сами значки — знак соцсети вне git: размеры
сверяются, только если файлы на месте.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import json
import re
import struct
import unittest

from fastapi.testclient import TestClient

from core import core as main
from core.config import env

ICONS = env.ROOT / "assets" / "icons"


def png_size(path) -> tuple[int, int]:
    """Ширина и высота PNG из заголовка IHDR."""
    head = path.read_bytes()[:24]
    return struct.unpack(">II", head[16:24])


class SiteIconTests(unittest.TestCase):
    def test_the_shell_links_the_tab_phone_and_manifest_icons(self):
        head = (env.ROOT / "index.html").read_text(encoding="utf-8").split("</head>")[0]
        links = dict(re.findall(r'<link rel="([a-z-]+)"[^>]*href="([^"]+)"', head))
        self.assertEqual(links["apple-touch-icon"], "/assets/icons/apple-touch-icon.png")
        self.assertEqual(links["manifest"], "/manifest.webmanifest")
        self.assertIn('href="/assets/icons/favicon-32.png"', head)
        self.assertIn('href="/assets/icons/favicon-48.png"', head)
        self.assertNotIn("data:image/svg+xml", head)  # прежний значок с буквой «A»

    def test_the_manifest_names_asoplay_with_its_colour_and_maskable_icons(self):
        manifest = json.loads((env.ROOT / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual((manifest["name"], manifest["short_name"], manifest["theme_color"]), ("AsoPlay", "AsoPlay", "#4c637b"))
        self.assertEqual([(i["src"], i["sizes"]) for i in manifest["icons"]],
                         [("/assets/icons/icon-192.png", "192x192"), ("/assets/icons/icon-512.png", "512x512")])
        self.assertTrue(all("maskable" in i["purpose"] for i in manifest["icons"]))

    def test_the_manifest_is_served_with_its_own_type(self):
        resp = TestClient(main.app).get("/manifest.webmanifest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "application/manifest+json")
        self.assertEqual(resp.json()["name"], "AsoPlay")

    def test_icon_files_have_the_sizes_the_page_and_manifest_promise(self):
        expected = {"favicon-32.png": (32, 32), "favicon-48.png": (48, 48), "apple-touch-icon.png": (180, 180),
                    "icon-192.png": (192, 192), "icon-512.png": (512, 512)}
        present = {name: size for name, size in expected.items() if (ICONS / name).is_file()}
        if not present:
            self.skipTest("значков нет в рабочей копии (они вне git)")
        self.assertEqual(present, expected)
        self.assertEqual({name: png_size(ICONS / name) for name in present}, expected)


if __name__ == "__main__":
    unittest.main()
