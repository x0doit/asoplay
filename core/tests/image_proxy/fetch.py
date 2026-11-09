"""
Выборка картинок: ответ не исполняется как страница, только 200 с типом из списка, без сжатия и не больше 8 МБ,
перенаправления — не больше трёх, с проверкой каждого шага и без перехода с https на http, ответ хоста без
второго прохода, сетевой сбой — с ним, варианты адреса — одна картинка.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import gzip
import zlib

import httpx

from core.classes import image_fetch
from core.tests.image_proxy.lib import MAL, PNG, ProxyCase, image

ANILIST = "https://s4.anilist.co/file/a.png"


class Chunks(httpx.AsyncByteStream):
    """Тело без длины, по мегабайту."""

    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        for start in range(0, len(self.data), 1 << 20):
            yield self.data[start:start + (1 << 20)]


class Answers(ProxyCase):
    async def test_an_image_comes_with_its_normalised_type_and_headers_that_keep_it_from_running_as_a_page(self):
        self.direct.answers[MAL] = lambda: image(content_type="IMAGE/JPG; charset=x")
        resp = await self.get()
        self.assertEqual((resp.status_code, resp.body, resp.media_type), (200, PNG, "image/jpeg"))
        self.assertEqual(resp.headers["content-security-policy"], "sandbox; default-src 'none'; style-src 'unsafe-inline'")
        self.assertEqual(resp.headers["x-content-type-options"], "nosniff")
        self.assertEqual(resp.headers["cache-control"], "public, max-age=86400, immutable")

    async def test_a_bad_or_foreign_address_is_refused_without_a_request(self):
        for url, status in (("https://[::1/a", 400), ("https://evil.test/a.png", 403), ("https://127.0.0.1/a.png", 403),
                            ("https://cdn.myanimelist.net:8443/a.png", 403), ("ftp://cdn.myanimelist.net/a", 403),
                            ("https://evil%2ecom.shikimori.io/a.png", 403)):
            self.assertEqual((await self.get(url)).status_code, status, url)
        self.assertEqual(self.direct.seen, [])

    async def test_only_a_listed_image_type_without_compression_and_only_up_to_the_limit(self):
        big = b"\x00" * (image_fetch.MAX_BYTES + 1)
        packed = gzip.compress(b"\x00" * (image_fetch.MAX_BYTES * 2))
        for answer in (lambda: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<script>"),
                       lambda: httpx.Response(200, content=b"no type"),
                       lambda: image(content_type="image/svg+xml"),
                       lambda: image(content_type="image/png,text/html"),
                       lambda: httpx.Response(200, headers=[(b"content-type", "image/png€".encode())], content=PNG),
                       lambda: image(packed, **{"content-encoding": "gzip"}),
                       lambda: image(gzip.compress(packed), **{"content-encoding": "gzip, gzip"}),
                       lambda: image(zlib.compress(PNG), **{"content-encoding": "deflate"}),
                       lambda: image(big),
                       lambda: httpx.Response(200, headers={"content-type": "image/png"}, stream=Chunks(big)),
                       lambda: httpx.Response(204)):
            self.direct.answers[MAL] = answer
            self.assertEqual((await self.get()).status_code, 502)
        self.direct.answers[MAL] = lambda: httpx.Response(404)
        self.assertEqual((await self.get()).status_code, 404)
        self.assertEqual(list(self.cache.iterdir()), [])
        last = self.direct.seen
        self.assertEqual(len(last), 12)  # каждый ответ — один запрос наверх, без второго прохода

    async def test_variants_of_one_address_are_one_image(self):
        self.direct.answers[MAL] = image
        for url in (MAL, MAL + "#1", MAL + "#2", "https://CDN.MyAnimeList.net./images/anime/1/1.jpg",
                    "https://cdn.myanimelist.net:443/images/anime/1/1.jpg", "https://@cdn.myanimelist.net/images/anime/1/1.jpg",
                    "https://cdn.myanimelist.net:+443/images/anime/1/1.jpg",
                    "https://cdn.myanimelist.net:4_43/images/anime/1/1.jpg"):
            resp = await self.get(url)
            self.assertEqual(getattr(resp, "body", None), PNG, url)
        self.assertEqual((self.direct.seen, len(list(self.cache.iterdir()))), ([MAL], 1))


class Redirects(ProxyCase):
    def hop(self, location):
        return lambda: httpx.Response(302, headers={"location": location})

    async def test_up_to_three_hops_between_allowed_hosts_are_followed(self):
        self.direct.answers.update({MAL: self.hop("https://myanimelist.net/a"), "https://myanimelist.net/a": self.hop("/b"),
                                    "https://myanimelist.net/b": self.hop(ANILIST), ANILIST: image})
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual(self.direct.seen, [MAL, "https://myanimelist.net/a", "https://myanimelist.net/b", ANILIST])

    async def test_a_hop_out_of_the_list_or_down_to_http_is_refused_before_it_is_sent(self):
        for target in ("https://evil.test/a.png", "http://127.0.0.1:8787/health", "http://169.254.169.254/",
                       "https://[::1]/", "https://cdn.myanimelist.net:22/", "file:///etc/passwd",
                       "http://cdn.myanimelist.net/images/anime/1/1.jpg"):
            self.direct.seen.clear()
            self.direct.answers[MAL] = self.hop(target)
            self.assertEqual((await self.get()).status_code, 403, target)
            self.assertEqual(self.direct.seen, [MAL], target)

    async def test_a_fourth_hop_or_a_broken_one_is_a_bad_gateway(self):
        self.direct.answers[MAL] = self.hop(MAL)
        self.assertEqual((await self.get()).status_code, 502)
        self.assertEqual(len(self.direct.seen), image_fetch.MAX_HOPS + 1)
        self.direct.answers[MAL] = self.hop("https://[::1/x")
        self.assertEqual((await self.get()).status_code, 502)


class Passes(ProxyCase):
    async def test_an_answer_of_the_host_gets_no_second_pass_but_a_network_failure_does(self):
        for status in (500, 503, 429):
            self.direct.seen.clear()
            self.direct.answers[MAL] = lambda: httpx.Response(status)
            self.assertEqual((await self.get()).status_code, status)
            self.assertEqual(len(self.direct.seen), 1, status)
        answers = iter([httpx.ConnectError("blip"), image()])

        def flaky():
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        self.direct.seen.clear()
        self.direct.answers[MAL] = flaky
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual(self.direct.seen, [MAL, MAL])
