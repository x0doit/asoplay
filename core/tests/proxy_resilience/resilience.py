"""
Запросы наружу: свежий кеш, один общий запрос, пауза после сбоев с отдачей старого кеша, сроки,
восстановление.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/proxy_resilience: вход (core/tests/test_proxy_resilience.py) собирает части по порядку.
"""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from core.classes import http, http_cache


class ProxyResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        http_cache._pending_fetches.clear()
        http_cache._upstream_retry_after.clear()
        http_cache._upstream_connect_failures.clear()
        self.client = Mock(request=AsyncMock(return_value=httpx.Response(200, json={"data": []})), aclose=AsyncMock())
        self.patches = [patch.object(http_cache, "cache_get", return_value=None),
                        patch.object(http_cache, "_cache_get_any", return_value=None),
                        patch.object(http_cache, "cache_put"),
                        patch.object(http, "get_client", new=AsyncMock(return_value=self.client)),
                        patch.object(http.vpn_bridge, "is_active", return_value=False)]
        self.mocks = [p.start() for p in self.patches]
        self.url = "https://graphql.anilist.co"

    async def asyncTearDown(self):
        for task in list(http_cache._pending_fetches.values()):
            task.cancel()
        await asyncio.gather(*list(http_cache._pending_fetches.values()), return_exceptions=True)
        http_cache._pending_fetches.clear()
        http_cache._upstream_retry_after.clear()
        http_cache._upstream_connect_failures.clear()
        for p in reversed(self.patches):
            p.stop()

    async def fetch(self, **kwargs):
        return await http_cache.cached_fetch("GET", self.url, ttl=60, **kwargs)

    async def test_fresh_cache_never_contacts_upstream(self):
        self.mocks[0].return_value = (b"cached", "application/json", 200)
        self.assertEqual((await self.fetch())[0], b"cached")
        self.client.request.assert_not_awaited()

    async def test_concurrent_identical_requests_share_one_upstream_call(self):
        await asyncio.gather(*[self.fetch() for _ in range(10)])
        self.client.request.assert_awaited_once()

    async def test_repeated_connect_failure_opens_cooldown_and_serves_cache(self):
        self.mocks[1].return_value = (b"stale", "application/json", 200)
        self.client.request.side_effect = httpx.ConnectError("offline")
        self.assertEqual((await self.fetch())[0], b"stale")
        self.assertEqual((await self.fetch())[0], b"stale")
        self.assertEqual((await self.fetch())[0], b"stale")
        self.assertEqual(self.client.request.await_count, 2)
        self.assertLessEqual(self.client.request.call_args.kwargs["timeout"].read, 2.5)
        self.client.aclose.assert_not_awaited()

    async def test_cold_failure_has_a_total_deadline(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(5)
        self.client.request.side_effect = slow
        start = time.monotonic()
        with self.assertRaises(HTTPException) as error:
            await self.fetch(timeout=0.03)
        self.assertEqual(error.exception.status_code, 504)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertNotIn("graphql.anilist.co", http_cache._upstream_retry_after)
        self.client.request.side_effect = None
        self.assertEqual((await self.fetch())[2], 200)

    async def test_healthy_response_after_old_3_5_second_deadline_is_accepted(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(3.6)
            return httpx.Response(200, json={"data": [1]})
        self.client.request.side_effect = slow
        self.assertEqual((await self.fetch(timeout=5))[2], 200)
        self.assertFalse(http_cache._upstream_retry_after)

    async def test_one_slow_endpoint_does_not_disable_another(self):
        self.mocks[1].return_value = (b"stale", "application/json", 200)
        self.client.request.side_effect = httpx.ReadTimeout("slow query")
        self.assertEqual((await self.fetch())[0], b"stale")
        self.assertFalse(http_cache._upstream_retry_after)
        self.client.request.side_effect = None
        self.assertEqual((await self.fetch(params={"page": 2}))[2], 200)

    async def test_graphql_errors_are_not_cached_as_success(self):
        self.client.request.return_value = httpx.Response(200, json={"errors": [{"message": "offline"}]})
        await http_cache.cached_fetch("POST", "https://graphql.anilist.co", ttl=60)
        self.mocks[2].assert_not_called()

    async def test_current_shiki_host_keeps_bounded_catalog_timeout(self):
        await http_cache.cached_fetch("GET", "https://shikimori.io/api/animes", ttl=60, timeout=12)
        self.assertEqual(self.client.request.call_args.kwargs["timeout"].read, 10)

    async def test_recovery_after_cooldown(self):
        http_cache._upstream_retry_after["graphql.anilist.co"] = time.monotonic() - 1
        self.assertEqual((await self.fetch())[2], 200)
        self.assertNotIn("graphql.anilist.co", http_cache._upstream_retry_after)

    async def test_missing_title_does_not_trip_circuit(self):
        self.client.request.return_value = httpx.Response(404, json={"error": "missing"})
        self.assertEqual((await self.fetch())[2], 404)
        self.assertNotIn("graphql.anilist.co", http_cache._upstream_retry_after)
        self.mocks[2].assert_not_called()

    async def test_cancelling_one_caller_does_not_cancel_shared_fetch(self):
        gate = asyncio.Event()
        async def delayed(*args, **kwargs):
            await gate.wait()
            return httpx.Response(200, json={"data": []})
        self.client.request.side_effect = delayed
        first = asyncio.create_task(self.fetch())
        second = asyncio.create_task(self.fetch())
        await asyncio.sleep(0.01)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        gate.set()
        self.assertEqual((await second)[2], 200)
        self.client.request.assert_awaited_once()
