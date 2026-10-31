"""
Каталог тайтлов и русский поиск: mal_id в каталоге, настоящий 404, отказ поставщика — 503; адреса
Shikimori и запасной поиск.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/proxy_resilience: вход (core/tests/test_proxy_resilience.py) собирает части по порядку.
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from core.api.sections import catalog as catalog_routes
from core.classes import http_cache
from core.modules import title_cache, title_pages


class TitleCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedded_catalog_keeps_mal_id(self):
        media = {"mal_id": 1535, "title": "Death Note", "score": 8.5, "genres": [{"mal_id": 7, "name": "Mystery"}]}
        fetch = AsyncMock(return_value=(json.dumps({"data": media}).encode(), "application/json", 200))
        with patch.object(title_cache.jikan_catalog, "fetch", fetch), patch.object(title_cache, "store_title", side_effect=lambda p: p):
            result = await title_cache.fetch_and_store(1535)
        self.assertEqual(result["mal_id"], 1535)
        self.assertEqual(result["score"], 8.5)
        self.assertEqual(result["title_en"], "Death Note")

    async def test_confirmed_404_is_not_replaced_with_reserve(self):
        fetch = AsyncMock(return_value=(b"{}", "application/json", 404))
        with patch.object(title_cache.jikan_catalog, "fetch", fetch):
            self.assertIsNone(await title_cache.fetch_and_store(2))
        fetch.assert_awaited_once()

    async def test_provider_outage_returns_503_not_false_404(self):
        with patch.object(title_cache, "load_cached", return_value=None), \
             patch.object(title_cache, "fetch_and_store", new=AsyncMock(side_effect=HTTPException(503))):
            result = await title_pages.canonical_title("1535")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.headers["X-Robots-Tag"], "noindex")


class RussianSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_shiki_routes_use_current_origin(self):
        media = {"id": 42310, "russian": "Title", "image": {"preview": "/system/animes/preview/42310.jpg"}}
        fetch = AsyncMock(side_effect=[
            (json.dumps(media).encode(), "application/json", 200),
            (json.dumps([media]).encode(), "application/json", 200),
            (json.dumps([media]).encode(), "application/json", 200),
        ])
        with patch.object(http_cache, "cached_fetch", fetch):
            detail = await catalog_routes.shiki_anime(42310)
            batch = await catalog_routes.shiki_batch("42310")
            search = await catalog_routes.shiki_search(q="Title", limit=24, page=1)
        self.assertEqual(json.loads(detail.body)["id"], 42310)
        self.assertEqual(batch["42310"]["russian"], "Title")
        self.assertEqual(json.loads(search.body)[0]["id"], 42310)
        self.assertEqual([call.args[1] for call in fetch.await_args_list], [
            "https://shikimori.io/api/animes/42310",
            "https://shikimori.io/api/animes",
            "https://shikimori.io/api/animes",
        ])

    async def test_shiki_image_proxy_accepts_current_host_but_not_lookalikes(self):
        fetch = AsyncMock(return_value=(b"image", "image/jpeg", 200))
        with patch.object(http_cache, "cached_fetch", fetch):
            image = await catalog_routes.proxy_img("https://shikimori.io/system/animes/preview/42310.jpg")
            self.assertEqual(image.body, b"image")
            with self.assertRaises(HTTPException) as error:
                await catalog_routes.proxy_img("https://shikimori.io.evil.example/image.jpg")
            self.assertEqual(error.exception.status_code, 403)
        fetch.assert_awaited_once()

    async def test_reserve_preserves_ids_titles_and_pagination(self):
        body = {"response": [{"anime_id": 999, "title": "Title", "remote_ids": {"myanimelist_id": 1535},
                              "poster": {"big": "//static.yani.tv/image.webp"}, "year": 2006},
                             {"anime_id": 1000, "title": "No MAL identity"}]}
        reserve = (json.dumps(body).encode(), "application/json", 200)
        fetch = AsyncMock(side_effect=[HTTPException(503), reserve])
        with patch.object(http_cache, "cached_fetch", fetch):
            result = await catalog_routes.shiki_search(q="Title", limit=2, page=3)
        self.assertEqual(result["data"][0]["id"], 1535)
        self.assertEqual(result["data"][0]["russian"], "Title")
        self.assertEqual(len(result["data"]), 1)
        self.assertTrue(result["data"][0]["image"]["original"].startswith("https://"))
        self.assertEqual(fetch.call_args.kwargs["params"]["offset"], 4)
        self.assertTrue(result["pagination"]["has_next_page"])

    async def test_broken_reserve_is_not_an_empty_search(self):
        fetch = AsyncMock(return_value=(b"{}", "application/json", 503))
        with patch.object(http_cache, "cached_fetch", fetch):
            with self.assertRaises(HTTPException):
                await catalog_routes._yummy_search("Title", 24, 1)
