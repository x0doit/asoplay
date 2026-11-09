"""
Кеш картинок: свежая отдаётся без запроса, старше срока — помечается старой, испорченная запись — как нет её,
запись — заменой файла целиком; размер считается с первой записи, и переполнение убирается сразу — самые давно
нужные файлы первыми; картинка из кеша освежает свой файл, и сбой этого не превращает попадание в промах.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import os
import time
from unittest.mock import patch

from core.classes import image_cache, image_proxy
from core.tests.image_proxy.lib import MAL, PNG, ProxyCase, expire, image


class Cache(ProxyCase):
    async def test_a_fresh_copy_is_served_without_a_request_and_an_old_one_is_marked_old(self):
        self.direct.answers[MAL] = image
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual(self.direct.seen, [MAL])
        key = image_proxy.canonical(MAL)
        self.assertEqual(image_cache.lookup(key), (PNG, "image/png", True))
        expire(image_cache.path(key))
        self.assertEqual(image_cache.lookup(key), (PNG, "image/png", False))

    async def test_a_broken_record_counts_as_missing_and_a_write_leaves_no_temporary_file(self):
        self.direct.answers[MAL] = image
        file = image_cache.path(image_proxy.canonical(MAL))
        file.parent.mkdir(parents=True, exist_ok=True)
        for broken in (b"", b"not json\n" + PNG, b'{"type": "image/png"}\n' + PNG, b"[1]\n" + PNG):
            file.write_bytes(broken)
            self.assertIsNone(image_cache.lookup(image_proxy.canonical(MAL)), broken)
        file.write_bytes(b'{"type": "text/html", "at": %d}\n<script>' % int(time.time()))  # не из списка типов
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual([p.suffix for p in self.cache.iterdir()], [".img"])

    def test_the_first_write_counts_the_folder_and_an_overflow_is_cleaned_at_once(self):
        now = time.time()
        for n in range(4):  # уже лежали до запуска, самые старые — первыми
            (self.cache / f"old{n}.img").write_bytes(b"\x00" * 1000)
            os.utime(self.cache / f"old{n}.img", (now, now - (10 - n) * 86400))
        with patch.object(image_cache, "MAX_CACHE_BYTES", 5000):
            image_cache.store("https://cdn.myanimelist.net/a.jpg", b"\x00" * 900, "image/jpeg")
            self.assertEqual(sorted(p.name for p in self.cache.iterdir() if p.name.startswith("old")),
                             ["old0.img", "old1.img", "old2.img", "old3.img"])  # 5 КБ без малого — в пределе
            image_cache.store("https://cdn.myanimelist.net/b.jpg", b"\x00" * 900, "image/jpeg")
            left = sorted(p.name for p in self.cache.iterdir() if p.name.startswith("old"))
            self.assertEqual(left, ["old2.img", "old3.img"])  # сразу, до LOW_WATER: самые давние ушли
            size = sum(p.stat().st_size for p in self.cache.iterdir())
            self.assertLessEqual(size, 5000 * image_cache.LOW_WATER)
            self.assertEqual(image_cache._size, size)
            image_cache.store("https://cdn.myanimelist.net/a.jpg", b"\x00" * 100, "image/jpeg")  # замена — разница
            self.assertEqual(image_cache._size, sum(p.stat().st_size for p in self.cache.iterdir()))

    def test_a_failed_folder_scan_leaves_the_size_unknown_not_zero(self):
        with patch.object(image_cache.os, "scandir", side_effect=PermissionError("locked")):
            self.assertIsNone(image_cache.cleanup())
            image_cache.store(MAL, PNG, "image/png")
        self.assertIsNone(image_cache._size)  # следующая запись обойдёт папку снова
        image_cache.store(MAL + "?2", PNG, "image/png")
        self.assertEqual(image_cache._size, sum(p.stat().st_size for p in self.cache.iterdir()))

    def test_a_copy_in_use_refreshes_its_file_once_a_day_and_a_failure_to_do_it_is_still_a_hit(self):
        image_cache.store(MAL, PNG, "image/png")
        file = image_cache.path(MAL)
        os.utime(file, (time.time(), time.time() - 3 * 86400))
        image_cache.lookup(MAL)
        self.assertLess(time.time() - file.stat().st_mtime, 60)
        os.utime(file, (time.time(), time.time() - 3600))
        image_cache.lookup(MAL)
        self.assertGreater(time.time() - file.stat().st_mtime, 3000)  # моложе суток — не трогается
        os.utime(file, (time.time(), time.time() - 3 * 86400))
        with patch.object(image_cache.os, "utime", side_effect=PermissionError("read-only")):
            self.assertEqual(image_cache.lookup(MAL), (PNG, "image/png", True))
