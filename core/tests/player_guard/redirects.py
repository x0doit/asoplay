"""
Перенаправления прокси плеера — вручную: каждый шаг проверяется до отправки, брошенные ответы закрываются,
303 продолжается как GET, неразборчивый Location и бесконечная цепочка — 502, у чистых кадров — их ошибка.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/player_guard: вход (core/tests/test_player_guard.py) собирает части по порядку.
"""
import unittest

import httpx
from fastapi import HTTPException

from core.modules.player import routes as player
from core.tests.player_guard.stubs import KODIK, Body, Upstream, request


class Redirects(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, answers, url=KODIK, method="GET"):
        """Ответ _fetch (или его HTTPException) и запросы, дошедшие до апстрима."""
        upstream = Upstream(answers)
        with upstream.patched():
            try:
                result = await player._fetch(request(method), url, url)
            except HTTPException as exc:
                result = exc
        return result, [str(item.url) for item in upstream.seen], [item.method for item in upstream.seen]

    async def test_hop_to_internal_or_foreign_host_is_refused_before_it_is_sent(self):
        for target in ("http://127.0.0.1:8787/health", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
                       "https://example.com/", "https://api.yani.tv/"):
            first = Body(b"moved")
            answers = {KODIK: lambda: httpx.Response(302, headers={"location": target}, stream=first)}
            result, seen, _ = await self.fetch(answers)
            self.assertEqual(result.status_code, 403, target)
            self.assertEqual(seen, [KODIK], target)
            self.assertTrue(first.closed, target)

    async def test_hops_inside_the_list_are_followed_and_left_responses_closed(self):
        first, video = Body(b""), "https://p12.solodcdn.com/v.mp4"
        answers = {KODIK: lambda: httpx.Response(302, headers={"location": video}, stream=first),
                   video: lambda: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"video")}
        result, seen, _ = await self.fetch(answers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(seen, [KODIK, video])
        self.assertTrue(first.closed)
        self.assertEqual(b"".join([chunk async for chunk in result.body_iterator]), b"video")

    async def test_post_after_303_continues_as_get(self):
        ftor = "https://kodikplayer.com/ftor"
        answers = {ftor: lambda: httpx.Response(303, headers={"location": "/ftor2"}),
                   ftor + "2": lambda: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")}
        result, seen, methods = await self.fetch(answers, url=ftor, method="POST")
        self.assertEqual(result.status_code, 200)
        self.assertEqual((seen, methods), ([ftor, ftor + "2"], ["POST", "GET"]))

    async def test_unparsable_location_is_502_bad_redirect(self):
        # httpx строит следующий шаг сам и не разбирает такой Location: это сбой апстрима, а не 500
        for target in ("javascript:alert(1)", "data:text/html,<b>x</b>"):
            answers = {KODIK: lambda: httpx.Response(302, headers={"location": target})}
            result, seen, _ = await self.fetch(answers)
            self.assertEqual(result.status_code, 502, target)
            self.assertIn("bad redirect", result.detail, target)
            self.assertEqual(seen, [KODIK], target)

    async def test_clean_frames_show_their_error_on_a_bad_redirect(self):
        embed = "https://vk.com/video_ext.php?oid=-1&id=2&hd=2"
        upstream = Upstream({embed: lambda: httpx.Response(302, headers={"location": "javascript:alert(1)"})})
        with upstream.patched():
            result = await player.frame(request(path="/player/frame"), "https://vk.com/video_ext.php?oid=-1&id=2")
        self.assertEqual(result.status_code, 200)
        self.assertIn("Видео не загрузилось", result.body.decode())

    async def test_endless_redirects_end_in_502(self):
        answers = {f"https://kodikplayer.com/{i}": (lambda i=i: httpx.Response(302, headers={"location": f"/{i + 1}"}))
                   for i in range(player._MAX_REDIRECTS + 5)}
        result, seen, _ = await self.fetch(answers, url="https://kodikplayer.com/0")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(len(seen), player._MAX_REDIRECTS + 1)
