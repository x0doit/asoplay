"""
Прокси плеера ходит только к хостам плееров: белый список на всех входах, перенаправления вручную с проверкой
каждого шага, публичные адреса после разрешения имени, CSP sandbox в документах, журнал отказов с ограничением.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import socket
import unittest
from unittest.mock import AsyncMock, patch

import httpcore
import httpx
from fastapi import HTTPException
from starlette.requests import Request

from core.classes import http_public
from core.functions.player_hosts import proxy_url_refusal
from core.modules.player import routes as player

KODIK = "https://kodikplayer.com/serial/1/abc/720p"
AKSOR = "https://player.aksor.tv/video/1"
SANDBOX = "sandbox allow-scripts allow-presentation"
# Внутренние адреса и IP-литералы: по имени их нет в списке, а адреса — не публичные
INTERNAL = ("http://127.0.0.1:8787/health", "http://10.0.0.1/", "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://8.8.8.8/", "http://2130706433/", "http://127.1/")
# Чужие хосты и обходы сравнения: похожее имя, логин в адресе, сайт и API Yummy на yani.tv
FOREIGN = ("https://example.com/", "https://kodikplayer.com.evil.test/", "https://evilkodikplayer.com/",
           "https://kodikplаyer.com/", "https://yani.tv/", "https://api.yani.tv/v1/anime",
           "https://kodikplayer.com@evil.test/", "https://user:pw@kodikplayer.com/", "https://evil.test\\@kodikplayer.com/")


def request(method="GET", path="/player/proxy", raw_path=None, query=b""):
    """Запрос браузера к прокси с пустым телом."""
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": method, "path": path, "raw_path": raw_path or path.encode(),
                    "query_string": query, "headers": []}, receive)


class Body(httpx.AsyncByteStream):
    """Тело ответа, которое помнит, закрыли ли его."""

    def __init__(self, data: bytes):
        self.data, self.closed = data, False

    async def __aiter__(self):
        yield self.data

    async def aclose(self):
        self.closed = True


class Upstream:
    """Подставной интернет для клиента плеера: ответ — по адресу запроса, все запросы записываются."""

    def __init__(self, answers=None):
        self.answers, self.seen = answers or {}, []

    def __call__(self, req):
        self.seen.append(req)
        return self.answers[str(req.url)]()

    def patched(self):
        """Клиент плеера и adblock подменены: сеть не трогается, реклама не мешает."""
        client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return _Patched(client)


class _Patched:
    """Контекст с подменёнными клиентом плеера и adblock."""

    def __init__(self, client):
        self.patches = (patch.object(player, "_get_client", AsyncMock(return_value=client)),
                        patch.object(player.adblock, "should_block", return_value=(False, "")))

    def __enter__(self):
        for item in self.patches:
            item.start()

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()


def addresses(*ips):
    """Ответ getaddrinfo с этими адресами, IPv4 и IPv6."""
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0)) if ":" in ip
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class Recorder(httpcore.AsyncMockBackend):
    """Подставной сетевой слой под проверкой: записывает, к чему подключались, и отвечает буфером."""

    def __init__(self, buffer):
        super().__init__(buffer)
        self.hosts = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append(host)
        return await super().connect_tcp(host, port, timeout, local_address, socket_options)


class HostList(unittest.TestCase):
    def test_player_hosts_and_their_subdomains_pass(self):
        for url in (KODIK, AKSOR, "https://KODIKPLAYER.COM./x", "https://p12.solodcdn.com/f/x.ts",
                    "https://alloha.yani.tv/?token_movie=1", "https://aksor.yani.tv/v/1", "https://iv.okcdn.ru/preview",
                    "https://plapi.cdnvideohub.com/api/v1", "https://fonts.gstatic.com/s/a.woff2",
                    "https://cdn.vkvideo.cloud/0/abc/master.m3u8", "https://dv97.sibnet.ru/v.mp4"):
            self.assertIsNone(proxy_url_refusal(url), url)

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


class Redirects(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, answers, url=KODIK, method="GET"):
        """Ответ _fetch (или его HTTPException) и запросы, дошедшие до апстрима."""
        upstream = Upstream(answers)
        with upstream.patched():
            try:
                result = await player._fetch(request(method), url, url)
            except HTTPException as exc:
                result = exc
        return result, [str(item.url) for item in upstream.seen], [item.method for item in upstream.seen]

    async def test_hop_to_internal_or_foreign_host_is_refused_before_it_is_sent(self):
        for target in ("http://127.0.0.1:8787/health", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
                       "https://example.com/", "https://api.yani.tv/"):
            first = Body(b"moved")
            answers = {KODIK: lambda: httpx.Response(302, headers={"location": target}, stream=first)}
            result, seen, _ = await self.fetch(answers)
            self.assertEqual(result.status_code, 403, target)
            self.assertEqual(seen, [KODIK], target)
            self.assertTrue(first.closed, target)

    async def test_hops_inside_the_list_are_followed_and_left_responses_closed(self):
        first, video = Body(b""), "https://p12.solodcdn.com/v.mp4"
        answers = {KODIK: lambda: httpx.Response(302, headers={"location": video}, stream=first),
                   video: lambda: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"video")}
        result, seen, _ = await self.fetch(answers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(seen, [KODIK, video])
        self.assertTrue(first.closed)
        self.assertEqual(b"".join([chunk async for chunk in result.body_iterator]), b"video")

    async def test_post_after_303_continues_as_get(self):
        ftor = "https://kodikplayer.com/ftor"
        answers = {ftor: lambda: httpx.Response(303, headers={"location": "/ftor2"}),
                   ftor + "2": lambda: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")}
        result, seen, methods = await self.fetch(answers, url=ftor, method="POST")
        self.assertEqual(result.status_code, 200)
        self.assertEqual((seen, methods), ([ftor, ftor + "2"], ["POST", "GET"]))

    async def test_endless_redirects_end_in_502(self):
        answers = {f"https://kodikplayer.com/{i}": (lambda i=i: httpx.Response(302, headers={"location": f"/{i + 1}"}))
                   for i in range(player._MAX_REDIRECTS + 5)}
        result, seen, _ = await self.fetch(answers, url="https://kodikplayer.com/0")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(len(seen), player._MAX_REDIRECTS + 1)


class NetworkLayer(unittest.IsolatedAsyncioTestCase):
    def test_public_addresses(self):
        for ip in ("93.184.216.34", "2606:2800:220:1::1", "64:ff9b::808:808"):
            self.assertTrue(http_public.is_public(ip), ip)
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                   "224.0.0.1", "240.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "ff02::1",
                   "::ffff:10.0.0.1", "64:ff9b::a00:1", "2002:7f00:1::1"):
            self.assertFalse(http_public.is_public(ip), ip)

    async def test_name_with_any_non_public_address_is_refused_before_connecting(self):
        for ips in (("10.0.0.5",), ("127.0.0.1",), ("::ffff:127.0.0.1",), ("169.254.169.254",), ("::1",),
                    ("100.64.0.1",), ("93.184.216.34", "10.0.0.5"), ("2606:2800:220:1::1", "fe80::1")):
            inner = Recorder([b""])
            layer = http_public.PublicOnlyBackend(inner)
            with patch.object(http_public.anyio, "getaddrinfo", AsyncMock(return_value=addresses(*ips))):
                with self.assertRaises(http_public.Refused, msg=ips):
                    await layer.connect_tcp("kodikplayer.com", 443)
            self.assertEqual(inner.hosts, [], ips)

    async def test_public_name_connects_to_its_checked_address_not_the_name(self):
        inner = Recorder([b""])
        layer = http_public.PublicOnlyBackend(inner)
        with patch.object(http_public.anyio, "getaddrinfo",
                          AsyncMock(return_value=addresses("93.184.216.34", "2606:2800:220:1::1"))):
            await layer.connect_tcp("kodikplayer.com", 443)
        self.assertEqual(inner.hosts, ["2606:2800:220:1::1"])  # первым — IPv6, как у anyio

    async def test_player_client_checks_the_address_of_every_hop(self):
        # Шаг на имя из списка, у которого адрес внутренний: отказ сетевого слоя, к нему не подключаются
        transport = http_public.transport()
        layer = transport._pool._network_backend
        self.assertIsInstance(layer, http_public.PublicOnlyBackend)
        layer._inner = inner = Recorder(
            [b"HTTP/1.1 302 Found\r\nLocation: https://cdn.kodikplayer.com/x\r\nContent-Length: 0\r\n\r\n"])
        dns = {"kodikplayer.com": addresses("93.184.216.34"), "cdn.kodikplayer.com": addresses("10.0.0.5")}

        async def resolve(host, port, **kwargs):
            return dns[host]

        with _Patched(httpx.AsyncClient(transport=transport)), \
             patch.object(http_public.anyio, "getaddrinfo", resolve):
            with self.assertRaises(HTTPException) as caught:
                await player._fetch(request(), "https://kodikplayer.com/x", "https://kodikplayer.com/x")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(inner.hosts, ["93.184.216.34"])

    async def test_running_player_client_has_the_layer(self):
        await player.shutdown()
        try:
            client = await player._get_client()
            self.assertIsInstance(client._transport._pool._network_backend, http_public.PublicOnlyBackend)
            self.assertFalse(client.follow_redirects)
        finally:
            await player.shutdown()


class Sandbox(unittest.IsolatedAsyncioTestCase):
    def assert_sandboxed(self, csp):
        self.assertTrue(csp.endswith("; " + SANDBOX), csp)
        self.assertNotIn("allow-same-origin", csp)

    async def test_documents_and_clean_frames_are_sandboxed(self):
        upstream = Upstream({AKSOR: lambda: httpx.Response(200, headers={"content-type": "text/html"},
                                                           content=b"<head></head><body></body>")})
        with upstream.patched():
            result = await player._fetch(request(), AKSOR, AKSOR, frame=True)
        self.assert_sandboxed(result.headers["content-security-policy"])
        frame = player._clean_video_frame("https://vd1.okcdn.ru/v.mp4", "https://vk.com/", element_id="av-test")
        self.assert_sandboxed(frame.headers["content-security-policy"])


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


if __name__ == "__main__":
    unittest.main()
