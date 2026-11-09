"""
Кадр плеера и чат серии: перемотку и полный экран кадр принимает только от страницы сайта. Адреса страниц ставит
сервер — сайт, каким его видит браузер, AV_SITE_URL и AV_ALLOWED_ORIGINS (как у живого соединения), без «*» и без
повторов; в кадр они уходят JSON-массивом, «</» внутри экранируется.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.modules.player import routes as player


def _request(host: str, proto: str = "") -> SimpleNamespace:
    """Запрос кадра с заголовками Host и X-Forwarded-Proto."""
    headers = {"host": host, **({"x-forwarded-proto": proto} if proto else {})}
    return SimpleNamespace(headers=headers, url=SimpleNamespace(scheme="http"))


class PageOrigins(unittest.TestCase):
    def test_site_as_the_browser_sees_it_then_site_url_then_allowed_without_star_or_repeats(self):
        with patch.object(player.config, "SITE_URL", "https://play.example"), \
                patch.object(player.config, "ALLOWED_ORIGINS", ["*", "https://play.example/", "http://localhost:8787"]):
            got = player._page_origins(_request("play.example", "https"))
        self.assertEqual(got, ["https://play.example", "http://localhost:8787"])

    def test_local_frame_from_localhost_still_accepts_the_127_page(self):
        with patch.object(player.config, "SITE_URL", "http://127.0.0.1:8787"), \
                patch.object(player.config, "ALLOWED_ORIGINS", []):
            got = player._page_origins(_request("localhost:8787"))
        self.assertEqual(got, ["http://localhost:8787", "http://127.0.0.1:8787"])

    def test_a_bad_host_header_adds_nothing(self):
        with patch.object(player.config, "SITE_URL", "https://play.example"), \
                patch.object(player.config, "ALLOWED_ORIGINS", []):
            got = player._page_origins(_request("evil.example/<x>", "https"))
        self.assertEqual(got, ["https://play.example"])


class FrameCarriesOrigins(unittest.TestCase):
    def frame(self, origins):
        body = player._clean_video_frame("https://video.sibnet.ru/v/a.mp4", "https://video.sibnet.ru/shell.php",
                                         page_origins=origins).body.decode("utf-8")
        return re.search(r"const pageOrigins = (.*?);\n", body).group(1), body

    def test_origins_are_a_json_array_in_the_frame(self):
        value, body = self.frame(["https://play.example", "http://127.0.0.1:8787"])
        self.assertEqual(value, '["https://play.example", "http://127.0.0.1:8787"]')
        self.assertNotIn("@@", body)

    def test_no_origins_means_an_empty_list_so_seek_is_refused(self):
        self.assertEqual(self.frame(None)[0], "[]")

    def test_script_end_inside_is_escaped(self):
        value, _ = self.frame(["</script><b>"])
        self.assertNotIn("</script>", value)
        self.assertIn("<\\/script>", value)

    def test_the_frame_checks_source_and_origin_and_announces_seek(self):
        _, body = self.frame(["https://play.example"])
        self.assertIn("event.source !== window.parent", body)
        self.assertIn("pageOrigins.includes(event.origin)", body)
        self.assertIn('type: "asoplay:frame-ready", seek: true', body)
        self.assertIn('type: "asoplay:fullscreen"', body)


if __name__ == "__main__":
    unittest.main()
