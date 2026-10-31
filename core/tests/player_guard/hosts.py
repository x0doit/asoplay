"""
Белый список хостов прокси плеера: имена плееров и их поддомены проходят, внутренние адреса, IP-литералы, чужие
хосты и обходы сравнения — нет; отказ — 403 на всех входах до любого похода, реклама — 204, как раньше; журнал
отказов с ограничением.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/player_guard: вход (core/tests/test_player_guard.py) собирает части по порядку.
"""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.functions.player_hosts import proxy_url_refusal
from core.modules.player import routes as player
from core.tests.player_guard.stubs import AKSOR, FOREIGN, INTERNAL, KODIK, Upstream, request


class HostList(unittest.TestCase):
    def test_player_hosts_and_their_subdomains_pass(self):
        for url in (KODIK, AKSOR, "https://KODIKPLAYER.COM./x", "https://p12.solodcdn.com/f/x.ts",
                    "https://alloha.yani.tv/?token_movie=1", "https://aksor.yani.tv/v/1", "https://iv.okcdn.ru/preview",
                    "https://plapi.cdnvideohub.com/api/v1", "https://fonts.gstatic.com/s/a.woff2",
                    "https://cdn.vkvideo.cloud/0/abc/master.m3u8", "https://dv97.sibnet.ru/v.mp4"):
            self.assertIsNone(proxy_url_refusal(url), url)

    def test_plyr_only_by_its_cdn_name(self):
        # Заглушка Plyr в плеере Alloha; plyr.io целиком и похожие имена в список не входят
        self.assertIsNone(proxy_url_refusal("https://cdn.plyr.io/static/blank.mp4"))
        for url in ("https://plyr.io/", "https://evil.cdn.plyr.io.example/static/blank.mp4", "https://cdnplyr.io/"):
            self.assertEqual(proxy_url_refusal(url), "host not allowed", url)

    def test_internal_foreign_and_tricky_addresses_are_refused(self):
        for url in INTERNAL + FOREIGN + ("file:///etc/passwd", "https:///x", "//kodikplayer.com/x"):
            self.assertIsNotNone(proxy_url_refusal(url), url)
        self.assertEqual(proxy_url_refusal("http://169.254.169.254/"), "ip address")
        self.assertEqual(proxy_url_refusal("https://kodikplayer.com@evil.test/"), "credentials")
        self.assertEqual(proxy_url_refusal("https://evil.test\\@kodikplayer.com/"), "credentials")


class Entrances(unittest.IsolatedAsyncioTestCase):
    async def status(self, call):
        """Код отказа входа; запрос к апстриму при этом не уходит."""
        upstream = Upstream()
        with upstream.patched():
            with self.assertRaises(HTTPException) as caught:
                await call()
        self.assertEqual(upstream.seen, [])
        return caught.exception.status_code

    async def test_proxy_and_frame_refuse_internal_and_foreign_hosts(self):
        for url in INTERNAL + FOREIGN[:6]:
            self.assertEqual(await self.status(lambda: player.proxy(request(), url, None)), 403, url)
            self.assertEqual(await self.status(lambda: player.frame(request(path="/player/frame"), url)), 403, url)
        # Логин в адресе: прокси отказывает по списку (403), кадр — ещё раньше, как неверный адрес (400)
        for url in FOREIGN[6:]:
            self.assertEqual(await self.status(lambda: player.proxy(request(), url, None)), 403, url)
            self.assertEqual(await self.status(lambda: player.frame(request(path="/player/frame"), url)), 400, url)

    async def test_documents_and_modules_refuse_hosts_outside_the_list(self):
        for host in ("127.0.0.1:8787", "10.0.0.1", "169.254.169.254", "2130706433", "example.com", "api.yani.tv"):
            for kind, route in (("documents", player.player_document), ("modules", player.player_module)):
                path = f"/player/{kind}/https/{host}/x.js"
                status = await self.status(lambda: route(request(path=path), "https", host, "x.js"))
                self.assertEqual(status, 403, path)

    async def test_ads_still_get_204_before_the_list(self):
        # Скрипт рекламы отвечает пустым 204, как раньше: на 403 плееры ловят «блокировщик»
        with patch.object(player.adblock, "should_block", return_value=(True, "ads")), \
             patch.object(player, "_get_client", AsyncMock()) as client:
            result = await player.proxy(request(), "https://imasdk.googleapis.com/js/sdkloader/ima3.js", None)
        self.assertEqual(result.status_code, 204)
        client.assert_not_awaited()


class RefusalLog(unittest.TestCase):
    def test_one_line_per_host_a_minute_and_a_cap_for_all_hosts(self):
        refusals = player._RefusalLog()
        clock = [1000.0]
        with patch.object(player.time, "monotonic", lambda: clock[0]), \
             self.assertLogs(player.log, "INFO") as logs:
            for _ in range(5):
                refusals("evil.test", "host not allowed")
            for n in range(refusals.LINES + 10):
                refusals(f"h{n}.test", "host not allowed")
            clock[0] += refusals.WINDOW
            refusals("evil.test", "host not allowed")
        lines = logs.output
        self.assertEqual(len(lines), refusals.LINES + 1)
        self.assertIn("player proxy refused evil.test (host not allowed)", lines[0])
        self.assertIn("4 more for this host", lines[-1])
        self.assertIn("11 more for other hosts not logged", lines[-1])
