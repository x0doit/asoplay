"""
«Случайное» (/random/watchable) без сети: перемешанная страница каталога, фильтры, проверка в MAL и длительность,
повторы, ошибки и время. Каталог (http.request) и Jikan (jikan_catalog.fetch) подменены.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from core.api.sections import random as random_api
from core.functions import site_settings

HIDDEN = 999


def row(mal_id=1535, alias="tv", rating=8.5, title="Тетрадь смерти", status="released"):
    """Строка каталога old.yummyani.me."""
    return {"anime_id": (mal_id or 0) + 10, "title": title, "year": 2006, "type": {"alias": alias},
            "rating": {"average": rating}, "remote_ids": {"myanimelist_id": mal_id}, "anime_status": {"alias": status}}


def page(*rows):
    """Ответ каталога со строками."""
    return httpx.Response(200, json={"response": list(rows)})


def card(kind="TV", duration="23 min per ep"):
    """Ответ Jikan с карточкой тайтла."""
    return json.dumps({"data": {"type": kind, "duration": duration}}).encode(), "application/json", 200


MAL_404 = (b'{"data":null}', "application/json", 404)


class RandomTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p = patch.object(site_settings, "hidden", side_effect=lambda mal_id: mal_id == HIDDEN)
        p.start()
        self.addCleanup(p.stop)

    async def pick(self, pages, cards=()):
        """Нажать «Случайное»: ответ или код ошибки, вызовы каталога и Jikan."""
        self.request = AsyncMock(side_effect=list(pages))
        self.fetch = AsyncMock(side_effect=list(cards))
        with patch.object(random_api.http, "request", self.request), \
                patch.object(random_api.jikan_catalog, "fetch", self.fetch):
            try:
                return await random_api.random_watchable()
            except HTTPException as exc:
                return exc.status_code

    async def test_an_empty_catalog_is_asked_three_times_then_502(self):
        self.assertEqual(await self.pick([page(), page(), page()]), 502)
        self.assertEqual(self.request.await_count, 3)

    async def test_nothing_fitting_is_502_without_asking_mal(self):
        misfits = [row(mal_id=None), row(mal_id=HIDDEN), row(alias="music"), row(rating=5)]
        self.assertEqual(await self.pick([page(*misfits)] * 3), 502)
        self.assertEqual((self.request.await_count, self.fetch.await_count), (3, 0))

    async def test_the_first_fitting_title_comes_from_one_request(self):
        result = await self.pick([page(row(mal_id=None), row(mal_id=52991, alias="tv", title="Фрирен"), row())],
                                 [card()])
        self.assertEqual(result, {"mal_id": 52991, "anime_id": 53001, "title": "Фрирен", "year": 2006, "type": "tv"})
        self.assertEqual((self.request.await_count, self.fetch.await_count), (1, 1))

    async def test_announcements_are_skipped_but_released_and_ongoing_titles_pass(self):
        rows = [row(mal_id=1, status="announcement"), row(mal_id=2, status="released")]
        self.assertEqual((await self.pick([page(*rows)], [card()]))["mal_id"], 2)
        self.assertEqual(self.fetch.await_count, 1)  # анонс до MAL не доходит
        self.assertEqual((await self.pick([page(row(mal_id=3, status="ongoing"))], [card()]))["mal_id"], 3)

    async def test_a_broken_jikan_card_keeps_the_title(self):
        for body in (b"<html>", b"[]", b'{"data": "x"}'):  # не JSON, не объект, не карточка
            result = await self.pick([page(row(mal_id=20))], [(body, "application/json", 200)])
            self.assertEqual(result["mal_id"], 20, body)

    async def test_a_title_mal_does_not_know_is_skipped(self):
        result = await self.pick([page(row(mal_id=2), row(mal_id=20))], [MAL_404, card()])
        self.assertEqual(result["mal_id"], 20)

    async def test_a_mal_outage_does_not_invalidate_the_title(self):
        for code in (503, 504):  # недоступен и время вышло
            result = await self.pick([page(row(mal_id=20))], [HTTPException(code)])
            self.assertEqual(result["mal_id"], 20)

    async def test_short_titles_by_jikan_are_skipped(self):
        rows = [row(mal_id=1, alias="movie"), row(mal_id=2), row(mal_id=3)]
        result = await self.pick([page(*rows)], [card("Movie", "30 min"), card("TV", "5 min per ep"), card()])
        self.assertEqual(result["mal_id"], 3)

    async def test_a_long_movie_and_an_unknown_length_pass(self):
        movie = await self.pick([page(row(mal_id=4, alias="movie"))], [card("Movie", "1 hr 51 min")])
        self.assertEqual(movie["mal_id"], 4)
        self.assertEqual((await self.pick([page(row(mal_id=5))], [card("TV", "Unknown")]))["mal_id"], 5)

    async def test_a_network_error_moves_to_the_next_request(self):
        result = await self.pick([httpx.ConnectError("down"), page(row(mal_id=20))], [card()])
        self.assertEqual((result["mal_id"], self.request.await_count), (20, 2))

    async def test_a_bad_answer_moves_to_the_next_request(self):
        answers = [httpx.Response(503), httpx.Response(200, text="<html>"), page(row(mal_id=20))]
        result = await self.pick(answers, [card()])
        self.assertEqual((result["mal_id"], self.request.await_count), (20, 3))

    async def test_the_catalog_goes_direct_first_then_through_the_vpn(self):
        await self.pick([page(row(mal_id=20))], [card()])
        self.assertEqual(self.request.await_args.args, ("GET", random_api._CATALOG))

    async def test_out_of_time_is_504(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)

        with patch.object(random_api, "TIMEOUT", 0.05), patch.object(random_api.http, "request", slow):
            with self.assertRaises(HTTPException) as caught:
                await random_api.random_watchable()
        self.assertEqual(caught.exception.status_code, 504)


class DurationTests(unittest.TestCase):
    def test_jikan_durations(self):
        cases = {"1 hr 51 min": 111, "24 min per ep": 24, "45 sec per ep": 0.75, "2 hr": 120, "Unknown": None, "": None}
        for text, minutes in cases.items():
            self.assertEqual(random_api._minutes(text), minutes, text)


if __name__ == "__main__":
    unittest.main()
