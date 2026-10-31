"""
CSP sandbox прокси плеера: документы и чистый кадр — с флагами iframe страницы, без allow-same-origin; ответы,
которые браузер может открыть как страницу, — sandbox без флагов; подресурсы — без CSP.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/player_guard: вход (core/tests/test_player_guard.py) собирает части по порядку.
"""
import unittest

import httpx

from core.modules.player import routes as player
from core.tests.player_guard.stubs import AKSOR, SANDBOX, Upstream, request


class Sandbox(unittest.IsolatedAsyncioTestCase):
    def assert_sandboxed(self, csp):
        self.assertTrue(csp.endswith("; " + SANDBOX), csp)
        self.assertNotIn("allow-same-origin", csp)

    async def test_documents_and_clean_frames_are_sandboxed(self):
        upstream = Upstream({AKSOR: lambda: httpx.Response(200, headers={"content-type": "text/html"},
                                                           content=b"<head></head><body></body>")})
        with upstream.patched():
            result = await player._fetch(request(), AKSOR, AKSOR, frame=True)
        self.assert_sandboxed(result.headers["content-security-policy"])
        frame = player._clean_video_frame("https://vd1.okcdn.ru/v.mp4", "https://vk.com/", element_id="av-test")
        self.assert_sandboxed(frame.headers["content-security-policy"])

    async def test_answers_a_browser_opens_as_a_page_get_a_bare_sandbox(self):
        # Вне документов плеера: страница ошибки, SVG, XML или ответ без типа, открытые напрямую, — без скриптов и
        # без origin сайта. Подресурсам (скрипты, стили, видео, плейлисты, JSON) CSP не нужен и не ставится.
        url = "https://p12.solodcdn.com/x"
        cases = [("text/html; charset=utf-8", 404, "sandbox"), ("application/xhtml+xml", 404, "sandbox"),
                 ("image/svg+xml", 200, "sandbox"), ("Image/SVG+XML; charset=UTF-8", 200, "sandbox"),
                 ("text/xml", 200, "sandbox"), ("application/xml", 200, "sandbox"), (None, 200, "sandbox"),
                 ("application/javascript", 200, None), ("application/json", 200, None), ("text/css", 200, None),
                 ("video/mp4", 200, None), ("text/plain; charset=utf-8", 200, None)]
        for content_type, status, csp in cases:
            headers = {"content-type": content_type} if content_type else {}
            upstream = Upstream({url: lambda: httpx.Response(status, headers=headers, content=b"#EXTM3U\n")})
            with upstream.patched():
                result = await player._fetch(request(), url, url)
            self.assertEqual(result.headers.get("content-security-policy"), csp, content_type)
