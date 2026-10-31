"""
Встроенный каталог Jikan: PHP-мост и закреплённая библиотека, запросы и фильтры, очереди и повторы.
Живую базу и историю просмотров не трогают, наружу не ходят. Нужны PHP и
core/classes/jikan/php/vendor (composer install).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException
from core.classes import http, http_cache
from core.classes.jikan import catalog as j, php_bridge


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        php_bridge._workers = asyncio.Semaphore(4)
        j._network = asyncio.Semaphore(2)
        http.MAL_LIMIT = http._RateLimiter(rps=1000)
        j._retry_after = 0
        j._pending.clear()
        saved = (j._hidden, j._visible)
        self.addCleanup(lambda: j.register_title_filter(hidden=saved[0], visible=saved[1]))
        j.register_title_filter(hidden=lambda mal_id: False, visible=lambda items: items)

    async def asyncTearDown(self):
        await j.shutdown()

    async def test_php_and_locked_library_are_available(self):
        result = await php_bridge.call({"mode": "health"})
        self.assertEqual(result["jikan"], "v5.0.0")

    async def test_official_request_keeps_genres_and_search_filters(self):
        p = {"genres": "26,46", "page": 2, "type": "tv", "status": "complete",
             "order_by": "score", "sort": "desc", "min_score": "7", "start_date": "2018-01-01"}
        d = await php_bridge.call({"mode": "describe", "path": "anime", "params": p})
        u = urlparse(d["url"])
        self.assertEqual(u.netloc, "myanimelist.net")
        q = parse_qs(u.query)
        for key, value in {"genre[]": ["26", "46"], "show": ["50"], "type": ["1"], "status": ["2"],
                           "o": ["3"], "w": ["1"], "score": ["7"], "sy": ["2018"]}.items():
            self.assertEqual(q[key], value)

    async def test_empty_mal_search_404_is_a_valid_empty_result(self):
        result = await php_bridge.call({"mode": "parse", "path": "anime", "params": {}, "status": 404, "html": ""})
        self.assertEqual(result, {"data": [], "has_next_page": False})

    async def test_pagination_has_no_gaps_when_sfw_removes_rows(self):
        rows = [{"mal_id": i, "rating": "Rx" if i % 7 == 0 else "PG-13"} for i in range(1, 121)]
        async def native(path, p):
            start = (p["page"] - 1) * 50
            return {"data": rows[start:start + 50], "has_next_page": start + 50 < len(rows)}
        with patch.object(j, "_native", new=AsyncMock(side_effect=native)):
            pages = [await j.catalog("anime", {"page": n, "limit": 24, "sfw": "true"}) for n in range(1, 6)]
        actual = [x["mal_id"] for page in pages for x in page["data"]]
        self.assertEqual(actual, [x["mal_id"] for x in rows if x["rating"] != "Rx"])
        self.assertFalse(pages[-1]["pagination"]["has_next_page"])

    async def test_season_excludes_continuing_and_filters_before_paging(self):
        rows = [{"mal_id": 1, "members": 99, "continuing": True, "type": "TV"},
                {"mal_id": 2, "members": 20, "type": "TV"}, {"mal_id": 3, "members": 30, "type": "Movie"},
                {"mal_id": 4, "members": 40, "type": "TV"}, {"mal_id": 5, "members": 50, "type": "TV", "r18": True}]
        with patch.object(j, "_native", new=AsyncMock(return_value={"data": rows})):
            result = await j.catalog("seasons/2026/fall", {"filter": "tv", "sfw": "true", "page": 2, "limit": 1})
        self.assertEqual(result["data"][0]["mal_id"], 2)
        self.assertEqual(result["pagination"]["last_visible_page"], 2)
        self.assertFalse(result["pagination"]["has_next_page"])

    async def test_title_lists_fail_clearly_without_the_hidden_title_filter(self):
        # Без строки регистрации в core.py список не должен молча показать скрытые тайтлы.
        j.register_title_filter(hidden=j._unregistered, visible=j._unregistered)
        rows = {"data": [{"mal_id": 1, "type": "TV"}], "has_next_page": False}
        with patch.object(j, "_native", new=AsyncMock(return_value=rows)):
            for path in ("anime", "top/anime", "seasons/2026/fall", "random/anime"):
                with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "register_title_filter"):
                    await j.catalog(path, {})
            episodes = await j.catalog("anime/5/episodes", {})
            card = await j.catalog("anime/5/full", {})
        self.assertEqual(episodes["data"], rows["data"])
        self.assertEqual(card["data"], rows["data"])

    async def test_details_share_full_cache_identity(self):
        native = AsyncMock(return_value={"data": {"mal_id": 1535}})
        with patch.object(j, "_native", native):
            await j.catalog("anime/1535")
        native.assert_awaited_once_with("anime/1535/full", {})

    async def test_missing_title_stays_404(self):
        with patch.object(j, "_native", new=AsyncMock(side_effect=HTTPException(404))):
            self.assertEqual((await j.fetch("anime/9999999"))[2], 404)

    async def test_failure_not_a_fake_empty_result(self):
        with patch.object(j, "_native", new=AsyncMock(side_effect=HTTPException(503))):
            with self.assertRaises(HTTPException) as e:
                await j.fetch("anime", {"genres": "26"})
        self.assertEqual(e.exception.status_code, 503)

    async def test_validation_happens_before_network_or_php(self):
        native = AsyncMock()
        with patch.object(j, "_native", native):
            for path, p in [("../.env", {}), ("anime", {"genres": "26&x=1"}), ("anime", {"page": 0}),
                            ("anime", {"min_score": "nan"}), ("anime", {"sort": "bogus"}),
                            ("anime", {"start_date": "2026-02-30"}), ("anime", {"url": "http://localhost"})]:
                with self.assertRaises(HTTPException):
                    await j.catalog(path, p)
        native.assert_not_awaited()

    async def test_concurrent_callers_share_parser_and_cache_write(self):
        native = AsyncMock(return_value={"data": [1]})
        with patch.object(http_cache, "cache_get", return_value=None), patch.object(http_cache, "cache_put") as put, \
             patch.object(j, "_fetch_native", native):
            results = await asyncio.gather(*[j._native("anime", {}) for _ in range(8)])
        self.assertEqual(results, [{"data": [1]}] * 8)
        native.assert_awaited_once()
        put.assert_called_once()

    async def test_cancellation_keeps_other_subscriber_alive(self):
        gate = asyncio.Event()
        async def native(*args):
            await gate.wait()
            return {"data": [1]}
        with patch.object(http_cache, "cache_get", return_value=None), patch.object(http_cache, "cache_put"), \
             patch.object(j, "_fetch_native", new=AsyncMock(side_effect=native)) as fetch:
            first = asyncio.create_task(j._native("anime", {}))
            second = asyncio.create_task(j._native("anime", {}))
            await asyncio.sleep(0.01)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            gate.set()
            self.assertEqual(await second, {"data": [1]})
            fetch.assert_awaited_once()

    async def test_stale_same_mal_data_survives_temporary_failure(self):
        cached = (b'{"data":[1]}', "application/json", 200)
        with patch.object(http_cache, "cache_get", side_effect=[None, cached]), patch.object(http_cache, "cache_put") as put, \
             patch.object(j, "_fetch_native", new=AsyncMock(side_effect=HTTPException(503))):
            self.assertEqual(await j._native("anime", {}), {"data": [1]})
        put.assert_not_called()

    async def test_process_timeout_kills_and_reaps_child(self):
        process = Mock(returncode=None, communicate=AsyncMock(side_effect=[asyncio.TimeoutError(), (b"", b"")]))
        with patch.object(php_bridge.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.TimeoutError):
                await php_bridge.call({"mode": "health"})
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.await_count, 2)

    async def test_rate_limit_response_prevents_immediate_new_network_request(self):
        client = Mock(request=AsyncMock(return_value=httpx.Response(429)))
        with patch.object(php_bridge, "call", new=AsyncMock(return_value={"url": "https://myanimelist.net/anime.php", "page_size": 50})), \
             patch.object(http, "get_client", new=AsyncMock(return_value=client)):
            for _ in range(2):
                with self.assertRaises(HTTPException):
                    await j._fetch_native("anime", {})
        client.request.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
