"""
Страницы тайтлов: кеш пишет только сервер из своего каталога. Маршрута, через который браузер записывал страницу,
нет; русское название — с Shikimori; устаревшая страница отдаётся из кеша и обновляется в фоне — один раз на тайтл
и не больше REFRESH_PER_MINUTE в минуту; каталог недоступен — запись остаётся прежней, попытка — при следующем
заходе. Без сети и без базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from core.classes import http_cache
from core.classes.jikan import catalog as jikan_catalog
from core.modules import title_cache, title_pages, title_render

JIKAN = {"data": {"mal_id": 1535, "title": "Death Note", "title_english": "Death Note", "title_japanese": "デスノート",
                  "synopsis": "A notebook.", "images": {"jpg": {"large_image_url": "https://cdn.myanimelist.net/1.jpg"}},
                  "year": 2006, "type": "TV", "status": "Finished Airing", "episodes": 37, "score": 8.62,
                  "genres": [{"name": "Mystery"}], "studios": [{"name": "Madhouse"}]}}
NOW = datetime(2026, 11, 2, 12, 0)


def page(fresh_until):
    return {"mal_id": 1535, "slug": "death-note", "fresh_until": fresh_until}


class Fresh(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        title_cache._refreshing.clear()
        title_cache._refreshed_at.clear()
        for p in (patch.object(title_cache, "utc_now", return_value=NOW),
                  patch.object(title_render, "render_title_page", side_effect=lambda info: HTMLResponse("page")),
                  patch.object(title_pages.site_settings, "hidden", return_value=False)):
            p.start()
            self.addCleanup(p.stop)

    async def settle(self):
        while title_cache._tasks:
            await asyncio.gather(*list(title_cache._tasks), return_exceptions=True)


class FromTheCatalogOnly(Fresh):
    def test_the_browser_record_route_is_gone(self):
        paths = {getattr(route, "path", "") for route in title_pages.router.routes}
        self.assertNotIn("/title-pages/record", paths)
        self.assertFalse(hasattr(title_pages, "RecordIn"))

    async def test_a_page_comes_from_jikan_with_the_russian_title_from_shikimori(self):
        stored = []

        async def jikan(path, timeout=10):
            return json.dumps(JIKAN).encode(), "application/json", 200

        async def shiki(method, url, **_):
            self.assertTrue(url.endswith("/api/animes/1535"))
            return json.dumps({"russian": "Тетрадь смерти"}).encode(), "application/json", 200
        with patch.object(jikan_catalog, "fetch", jikan), patch.object(http_cache, "cached_fetch", shiki), \
                patch.object(title_cache, "store_title", side_effect=lambda p: stored.append(p) or {"slug": "x"}):
            await title_cache.fetch_and_store(1535)
        (payload,) = stored
        self.assertEqual((payload["title_ru"], payload["title_en"], payload["episodes_total"]),
                         ("Тетрадь смерти", "Death Note", 37))
        self.assertEqual(payload["genres"], ["Mystery"])

    async def test_without_shikimori_the_page_keeps_the_catalog_title(self):
        stored = []

        async def jikan(path, timeout=10):
            return json.dumps(JIKAN).encode(), "application/json", 200

        async def shiki(*_, **__):
            raise OSError("shikimori is down")
        with patch.object(jikan_catalog, "fetch", jikan), patch.object(http_cache, "cached_fetch", shiki), \
                patch.object(title_cache, "store_title", side_effect=lambda p: stored.append(p) or {"slug": "x"}):
            await title_cache.fetch_and_store(1535)
        self.assertEqual((stored[0]["title_ru"], stored[0]["title_en"]), ("", "Death Note"))

    def test_storing_no_longer_touches_the_refresh_queue(self):
        sql = []

        class Cursor:
            def execute(self, query, params=()):
                sql.append(" ".join(query.split()))

            def fetchone(self):
                return None

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                pass
        with patch.object(title_cache, "connect", return_value=Conn()):
            title_cache.store_title({"mal_id": 1535, "title_en": "Death Note"})
        self.assertFalse(any("aviev_title_refresh_queue" in q for q in sql), sql)


class StalePages(Fresh):
    async def test_a_stale_page_is_served_from_the_cache_and_refreshed_once(self):
        calls = []

        async def fetch(mal_id):
            calls.append(mal_id)
            await asyncio.sleep(0.05)
        with patch.object(title_cache, "load_cached", return_value=page(NOW - timedelta(hours=1))), \
                patch.object(title_cache, "fetch_and_store", fetch):
            first = await title_pages.canonical_title("1535-death-note")
            second = await title_pages.canonical_title("1535-death-note")
            await self.settle()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(calls, [1535])

    async def test_a_fresh_page_is_not_refreshed(self):
        with patch.object(title_cache, "load_cached", return_value=page(NOW + timedelta(days=3))), \
                patch.object(title_cache, "refresh_later") as refresh:
            await title_pages.canonical_title("1535-death-note")
        refresh.assert_not_called()

    async def test_refreshes_are_limited_per_minute(self):
        async def fetch(mal_id):
            await asyncio.sleep(0.05)
        with patch.object(title_cache, "fetch_and_store", fetch):
            started = [title_cache.refresh_later(n) for n in range(1, title_cache.REFRESH_PER_MINUTE + 2)]
            await self.settle()
        self.assertEqual(started.count(True), title_cache.REFRESH_PER_MINUTE)
        self.assertFalse(started[-1])

    async def test_a_catalog_failure_keeps_the_old_page_and_tries_again_next_visit(self):
        async def down(mal_id):
            raise HTTPException(503, "Title catalog unavailable")
        with patch.object(title_cache, "fetch_and_store", down), \
                patch.object(title_cache, "store_title") as store, \
                self.assertLogs("asoplay.title_cache", "INFO") as logs:
            self.assertTrue(title_cache.refresh_later(1535))
            await self.settle()
            self.assertTrue(title_cache.refresh_later(1535))  # следующий заход пробует снова
            await self.settle()
        store.assert_not_called()
        self.assertTrue(any("kept as it was" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
