"""
Счётчик сбоев источника: что его остужает. Сбои связи (таймауты, ошибки httpx, ответы 5xx и 429) — три подряд;
ошибки разбора — у трёх разных тайтлов подряд, один кривой тайтл источник не остужает; быстрый отказ 4xx — никогда.
Успех сбрасывает обе серии. Тайтл запроса помнят ключи найденного.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/proxy_resilience: вход (core/tests/test_proxy_resilience.py) собирает части по порядку.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from core.functions import source_health
from core.functions import sources as source_registry


class SourceFaultTests(unittest.IsolatedAsyncioTestCase):
    SOURCE = "oldyummy"

    async def asyncSetUp(self):
        source_health._STATES.pop(self.SOURCE, None)
        self.bridge = patch.object(source_registry.vpn_bridge, "proxy_url", return_value=None)  # только прямой маршрут
        self.bridge.start()

    async def asyncTearDown(self):
        self.bridge.stop()
        source_health._STATES.pop(self.SOURCE, None)

    async def step(self, title, error=None):
        """Шаг источника в запросе тайтла title: error — чем шаг падает, None — успех."""
        async def run(route):
            if error is not None:
                raise error
            return ["hit"]

        token = source_registry.request_title.set(title)
        try:
            return await source_registry.source_call(self.SOURCE, "episodes", run, timeout=2)
        finally:
            source_registry.request_title.reset(token)

    async def broken(self, title):
        """Ошибка разбора у тайтла title: раздел ответил бы 502 только ему."""
        with self.assertRaises(ValueError):
            await self.step(title, ValueError("invalid literal for int(): '1168.5'"))

    async def test_parse_errors_of_three_different_titles_cool_the_source(self):
        for title in ("mal:1", "mal:2", "mal:3"):
            await self.broken(title)
        self.assertFalse(source_health.is_available(self.SOURCE))

    async def test_one_title_failing_three_times_does_not_cool_the_source(self):
        for _ in range(3):
            await self.broken("mal:21")  # «Ван-Пис»
        self.assertTrue(source_health.is_available(self.SOURCE))
        self.assertEqual(source_health._STATES[self.SOURCE].failures, 3)  # в «Обзоре» ошибки видны

    async def test_a_success_between_parse_errors_starts_the_streak_again(self):
        await self.broken("mal:1")
        self.assertEqual(await self.step("mal:2"), ["hit"])
        await self.broken("mal:3")
        await self.broken("mal:4")
        self.assertTrue(source_health.is_available(self.SOURCE))

    async def test_three_connection_failures_in_a_row_cool_the_source(self):
        for _ in range(3):
            with self.assertRaises(HTTPException) as error:
                await self.step("mal:21", httpx.ConnectError("connection refused"))
            self.assertEqual(error.exception.status_code, 502)
        self.assertFalse(source_health.is_available(self.SOURCE))
        with self.assertRaises(HTTPException) as error:
            await self.step("mal:22")
        self.assertEqual(error.exception.status_code, 503)  # остыл — для всех тайтлов

    async def test_5xx_and_429_count_as_outages_and_4xx_never_cools(self):
        request = httpx.Request("GET", "https://source.example.test/api")

        def answer(code):
            return httpx.HTTPStatusError(str(code), request=request, response=httpx.Response(code, request=request))

        for code, counts in ((500, 1), (503, 1), (429, 1), (404, 0), (403, 0)):
            source_health._STATES.pop(self.SOURCE, None)
            with self.subTest(code=code), self.assertRaises(httpx.HTTPStatusError):
                await self.step("mal:21", answer(code))
            self.assertEqual(source_health._STATES[self.SOURCE].consecutive_failures, counts)
        source_health._STATES.pop(self.SOURCE, None)
        for title in ("mal:1", "mal:2", "mal:3"):  # быстрый отказ — и у разных тайтлов
            with self.assertRaises(httpx.HTTPStatusError):
                await self.step(title, answer(403))
        self.assertTrue(source_health.is_available(self.SOURCE))

    async def test_a_guarded_step_counts_the_same_way(self):
        async def fail(error):
            raise error

        for title in ("mal:1", "mal:1", "mal:1", "mal:2", "mal:3"):
            token = source_registry.request_title.set(title)
            try:
                with self.assertRaises(KeyError):
                    await source_registry.guard_source(self.SOURCE, "dubs", fail(KeyError("data")), timeout=2)
            finally:
                source_registry.request_title.reset(token)
            self.assertEqual(source_health.is_available(self.SOURCE), title != "mal:3")

    async def test_found_keys_remember_their_title(self):
        token = source_registry.request_title.set("mal:21")
        try:
            key = source_registry.put({"type": "anime"})
        finally:
            source_registry.request_title.reset(token)
        self.assertEqual(source_registry.title_of(key), "mal:21")
        self.assertIsNone(source_registry.title_of(source_registry.put({"type": "anime"})))  # вне запроса тайтла нет

    async def test_search_keys_carry_the_mal_id(self):
        from core.api.sections.sources import search
        hit = SimpleNamespace(title="Ван-Пис", url="https://source.example.test/1", data={})
        with patch.object(search.site_settings, "source_enabled", return_value=True),                 patch.object(source_registry, "get_extractor"),                 patch.object(source_registry, "source_call", AsyncMock(return_value=[hit])):
            out = await search.search(q="One Piece", source="anilibria", mal_id=21)
        self.assertEqual(source_registry.title_of(out[0]["key"]), "mal:21")
