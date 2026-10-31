"""
Диагностика плеера: проверка CDN по фиксированным адресам и узкие правки SDK (Cast).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request
from core.modules.player import routes as player

PROBE = "https://cloud.solodcdn.com/test/" + "a" * 32 + ":2026102700/g"
BASE = "https://kodikplayer.com/serial/17542/hash/720p?episode=1"
CAST_URL = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js"
CAST_ENTRY = "F=function(a){a.length?E(a.shift(),function(){F(a)}):G()}"
CDN_CALL = ('$.ajax({url:cdnCheckLink,type:"GET",cache:!1,'
            'success:function(e){playerSettings.cdnIsWorking=!0},'
            'error:function(e){playerSettings.cdnIsWorking=!1}})')


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = Request({"type": "http", "method": "GET", "path": "/player/cdn-check",
                                "headers": [], "query_string": b""})

    async def check(self, status=200, error=None):
        cm = AsyncMock()
        if error:
            cm.__aenter__.side_effect = error
        else:
            cm.__aenter__.return_value = httpx.Response(status, headers={"location": "http://127.0.0.1/"})
        client = Mock(stream=Mock(return_value=cm))
        with patch.object(player, "_get_client", AsyncMock(return_value=client)):
            result = await player.kodik_cdn_check(self.request, PROBE, BASE)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertFalse(client.stream.call_args.kwargs["follow_redirects"])
        self.assertEqual(client.stream.call_args.args, ("GET", PROBE))
        return json.loads(result.body), cm

    async def test_available_cdn_stays_true(self):
        body, cm = await self.check()
        self.assertEqual(body, {"available": True, "upstream_status": 200, "reason": None})
        cm.__aexit__.assert_awaited_once()

    async def test_dns_failure_is_a_failed_check_not_a_fake_success(self):
        body, _ = await self.check(error=httpx.ConnectError("getaddrinfo failed"))
        self.assertEqual(body, {"available": False, "upstream_status": None, "reason": "network"})

    async def test_timeout_keeps_available_false(self):
        body, _ = await self.check(error=httpx.ReadTimeout("timeout"))
        self.assertFalse(body["available"])

    async def test_upstream_http_errors_keep_original_status_in_json(self):
        for status in (403, 404, 429, 500, 502, 503):
            body, cm = await self.check(status=status)
            self.assertFalse(body["available"])
            self.assertEqual(body["upstream_status"], status)
            cm.__aexit__.assert_awaited_once()

    async def test_probe_never_follows_a_redirect_to_an_arbitrary_host(self):
        body, _ = await self.check(status=302)
        self.assertFalse(body["available"])
        self.assertEqual(body["upstream_status"], 302)

    async def test_unrelated_urls_are_rejected_without_network_access(self):
        invalid = [
            "http://" + PROBE.removeprefix("https://"),
            PROBE.replace("cloud.solodcdn.com", "127.0.0.1"),
            PROBE.replace("cloud.solodcdn.com", "cloud.solodcdn.com.evil.test"),
            PROBE.replace("cloud.solodcdn.com", "user@cloud.solodcdn.com"),
            PROBE.replace("/test/", "/video/"),
            PROBE + "?redirect=http://127.0.0.1/",
            PROBE + "#fragment", PROBE.replace(":2026102700", ""),
        ]
        with patch.object(player, "_get_client", AsyncMock()) as client:
            for url in invalid:
                with self.assertRaises(HTTPException) as caught:
                    await player.kodik_cdn_check(self.request, url, BASE)
                self.assertEqual(caught.exception.status_code, 400)
            client.assert_not_awaited()

    async def test_probe_requires_a_kodik_base(self):
        with self.assertRaises(HTTPException) as caught:
            await player.kodik_cdn_check(self.request, PROBE, "https://example.test/")
        self.assertEqual(caught.exception.status_code, 400)

    async def test_real_media_failures_are_still_502(self):
        def offline(req):
            raise httpx.ConnectError("offline", request=req)

        client = httpx.AsyncClient(transport=httpx.MockTransport(offline))
        with patch.object(player, "_get_client", AsyncMock(return_value=client)), \
             patch.object(player.adblock, "should_block", return_value=(False, "")):
            with self.assertRaises(HTTPException) as caught:
                await player._fetch(self.request, "https://p12.solodcdn.com/episode.mp4", BASE)
        self.assertEqual(caught.exception.status_code, 502)

    def test_cache_busting_probe_query_is_supported(self):
        self.assertTrue(player._is_kodik_cdn_probe(PROBE + "?_=1793018358379"))

    def test_cast_hook_only_changes_the_official_loader(self):
        rewritten = player._rewrite_javascript(CAST_ENTRY, CAST_URL)
        self.assertIn("window.__asoplayPrepareCast(a)", rewritten)
        for url in ("https://evil.test/cv/js/sender/v1/cast_sender.js",
                    "https://www.gstatic.com/other.js", BASE):
            self.assertEqual(player._rewrite_javascript(CAST_ENTRY, url), CAST_ENTRY)

    def test_unknown_cast_loader_version_remains_unchanged(self):
        source = 'function newLoader(){return "SDK changed";}'
        self.assertEqual(player._rewrite_javascript(source, CAST_URL), source)

    def test_cdn_hook_is_scoped_and_keeps_original_fallback(self):
        rewritten = player._rewrite_javascript(CDN_CALL, BASE)
        self.assertIn("playerSettings.cdnIsWorking=available", rewritten)
        self.assertIn(":" + CDN_CALL, rewritten)
        self.assertEqual(player._rewrite_javascript(CDN_CALL, "https://other.test/app.js"), CDN_CALL)

    def test_other_kodik_requests_are_not_rewritten(self):
        source = '$.ajax({url:"https://cdn.example.test/episode.mp4"})'
        self.assertEqual(player._rewrite_javascript(source, BASE), source)


if __name__ == "__main__":
    unittest.main()
