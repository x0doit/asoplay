"""
ES-модули плеера через прокси: адреса зависимостей сохраняют папку источника.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import html
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, urljoin, urlsplit
import httpx

from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request
from core.modules.player import routes as player

BASE = "https://ru.yummyani.me/embed.html?id=123"
ENTRY = "https://ru.yummyani.me/assets/player.js"
# Страница плеера с хоста из белого списка: прокси ходит только к таким
PLAYER = "https://player.aksor.tv/embed.html?id=123"


class Body(httpx.AsyncByteStream):
    """Тело ответа, которое помнит, закрыли ли его."""

    def __init__(self, data: bytes):
        self.data, self.closed = data, False

    async def __aiter__(self):
        yield self.data

    async def aclose(self):
        self.closed = True


def mock_client(handler):
    """Клиент плеера на подставном транспорте: на каждый запрос отвечает handler, сеть не трогается."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class ModuleUrls(unittest.TestCase):
    def test_module_script_and_preload_have_canonical_paths(self):
        source = ('<head><script type="module" crossorigin src="/assets/player.js"></script>'
                  '<link rel="modulepreload" href="/assets/CNQ9Ug_H.js"></head>')
        rendered = player._rewrite_html(source, BASE)
        entry = player._module_proxy_url(ENTRY, BASE)
        chunk = player._module_proxy_url("/assets/CNQ9Ug_H.js", BASE)
        self.assertIn('src="' + entry + '"', rendered)
        self.assertIn('href="' + chunk + '"', rendered)
        self.assertEqual(urljoin(entry, "./CNQ9Ug_H.js"), chunk)

    def test_nested_modules_share_identity_regardless_of_importer(self):
        entry = player._module_proxy_url(ENTRY, BASE)
        child = urljoin(entry, "./nested/child.js")
        self.assertEqual(urljoin(child, "../shared.js"), urljoin(entry, "./shared.js"))

    def test_relative_paths_queries_and_encoding_survive(self):
        url = player._module_proxy_url("./dir/a%2Fb.js?x=1&amp;y=2#part", ENTRY)
        self.assertEqual(url, "/player/modules/https/ru.yummyani.me/assets/dir/a%2Fb.js?x=1&y=2#part")

    def test_module_preload_attributes_are_case_insensitive(self):
        result = player._rewrite_resource_tag("<LINK REL='MODULEPRELOAD' href='./assets/a.js'>", BASE)
        self.assertIn("/player/modules/https/ru.yummyani.me/assets/a.js", result)

    def test_classic_scripts_css_and_inline_code_are_unchanged(self):
        inline = 'const text = "import x from \'./not-a-module.js\'";'
        source = '<script src="/classic.js"></script><link rel="stylesheet" href="/style.css"><script>' + inline + '</script>'
        result = player._rewrite_html(source, BASE)
        self.assertIn(html.escape(player._proxy_url("/classic.js", BASE), quote=True), result)
        self.assertIn(html.escape(player._proxy_url("/style.css", BASE), quote=True), result)
        self.assertIn(inline, result)
        self.assertNotIn('/player/modules/https/ru.yummyani.me', result)

    def test_iframe_keeps_query_visible_to_the_upstream_sdk(self):
        result = player._rewrite_resource_tag('<iframe src="https://cdn.viqeo.tv/embed?vid=123&amp;playerId=7">', BASE)
        self.assertIn('/player/documents/https/cdn.viqeo.tv/embed?vid=123&amp;playerId=7', result)

    def test_rewritten_scripts_and_styles_drop_sri_hashes(self):
        # Alloha ships SRI hashes; the proxied copies differ, so the browser blocked the whole player.
        source = ('<head><link rel="stylesheet" href="/build/app.css" integrity="sha384-AAA">'
                  "<script src='/build/app.js' defer integrity='sha384-BBB'></script>"
                  '<script type="module" INTEGRITY=sha384-CCC src="/assets/m.js"></script>'
                  '<div data-integrity="keep"></div></head>')
        rendered = player._rewrite_html(source, BASE)
        self.assertNotRegex(rendered, r"(?i)\sintegrity\s*=")
        self.assertIn(html.escape(player._proxy_url("/build/app.js", BASE), quote=True), rendered)
        self.assertIn(player._module_proxy_url("/assets/m.js", BASE), rendered)
        self.assertIn('data-integrity="keep"', rendered)

class ModuleRoute(unittest.IsolatedAsyncioTestCase):
    def request(self, raw_path=b"/player/modules/https/ru.yummyani.me/assets/a%2Fb.js", query=b"x=1&x=2"):
        return Request({"type": "http", "method": "GET", "headers": [], "path": raw_path.decode(),
                        "raw_path": raw_path, "query_string": query})

    async def test_route_uses_existing_filtered_proxy_and_preserves_raw_url(self):
        req = self.request()
        with patch.object(player, "_fetch", AsyncMock(return_value=Response("export {}"))) as fetch:
            response = await player.player_module(req, "https", "ru.yummyani.me", "assets/a/b.js")
        url = "https://ru.yummyani.me/assets/a%2Fb.js?x=1&x=2"
        fetch.assert_awaited_once_with(req, url, url)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_invalid_origin_is_not_fetched(self):
        with patch.object(player, "_fetch", AsyncMock()) as fetch:
            for scheme, host in [('file', 'test'), ('https', 'a@b'), ('https', 'a\\b')]:
                with self.assertRaises(HTTPException) as caught:
                    await player.player_module(self.request(), scheme, host, 'a.js')
                self.assertEqual(caught.exception.status_code, 400)
            fetch.assert_not_awaited()

    async def test_adblock_still_runs_before_upstream_request(self):
        with patch.object(player.adblock, "should_block", return_value=(True, 'test')), \
             patch.object(player, "_get_client", AsyncMock()) as client:
            response = await player.player_module(self.request(), 'https', 'ru.yummyani.me', 'assets/a/b.js')
        self.assertEqual(response.status_code, 204)
        client.assert_not_awaited()

    async def test_upstream_failure_is_not_replaced_with_an_empty_script(self):
        with patch.object(player, "_fetch", AsyncMock(side_effect=HTTPException(502, 'offline'))):
            with self.assertRaises(HTTPException) as caught:
                await player.player_module(self.request(), 'https', 'ru.yummyani.me', 'assets/a/b.js')
        self.assertEqual(caught.exception.status_code, 502)

    async def test_frame_exposes_original_query_and_does_not_loop(self):
        url = PLAYER + '&title=Test%20title&preview=https%3A%2F%2Fimg.test%2Fa.jpg'
        req = self.request(raw_path=b'/player/frame', query=b'')
        with patch.object(player, '_fetch', AsyncMock(return_value=Response('frame'))) as fetch:
            redirected = await player.frame(req, url)
            self.assertEqual(redirected.status_code, 307)
            fetch.assert_not_awaited()
            target = urlsplit(redirected.headers['location'])
            self.assertEqual(target.path, '/player/documents/https/player.aksor.tv/embed.html')
            final_req = self.request(raw_path=target.path.encode('ascii'), query=target.query.encode('ascii'))
            result = await player.player_document(final_req, 'https', 'player.aksor.tv', 'embed.html')
            self.assertEqual(result.status_code, 200)
            fetch.assert_awaited_once_with(final_req, url, url, frame=True, referer=None)
            self.assertEqual(final_req.query_params['id'], '123')
            self.assertEqual(final_req.query_params['title'], 'Test title')

    async def test_alloha_page_is_requested_as_embedded_on_yummy(self):
        # Alloha prints the Referer into the page as the domain its token is checked against.
        url = 'https://alloha.yani.tv/?token_movie=abc&translation=79&season=1&episode=1&token=t'
        req = self.request(raw_path=b'/player/documents/https/alloha.yani.tv/', query=urlsplit(url).query.encode())
        with patch.object(player, '_fetch', AsyncMock(return_value=Response('frame'))) as fetch:
            await player.player_document(req, 'https', 'alloha.yani.tv', '')
        fetch.assert_awaited_once_with(req, url, url, frame=True, referer='https://ru.yummyani.me/')

    def test_upstream_gets_one_user_agent_and_alloha_signature(self):
        req = Request({"type": "http", "method": "POST", "path": "/player/proxy", "query_string": b"",
                       "headers": [(b"user-agent", b"Browser/1"), (b"borth", b"sig"), (b"cookie", b"x=1")]})
        headers = player._headers_for_upstream(req, 'https://alloha.yani.tv/bnsi/movies/1', BASE)
        self.assertEqual([v for k, v in headers.items() if k.lower() == 'user-agent'], ['Browser/1'])
        self.assertEqual(headers.get('borth'), 'sig')
        self.assertNotIn('cookie', {k.lower() for k in headers})

    def test_frame_csp_names_the_site_origin_for_safari(self):
        # Safari refused Kodik's /ftor under 'self' in the opaque sandboxed frame.
        def req(host):
            return Request({"type": "http", "method": "GET", "path": "/season/1", "query_string": b"", "scheme": "http",
                            "server": ("127.0.0.1", 8787), "headers": [(b"host", host), (b"x-forwarded-proto", b"https")]})
        origin = player._site_origin(req(b"play.example.test"))
        self.assertEqual(origin, "https://play.example.test")
        upstream = httpx.Response(200, headers={"content-type": "text/html"})
        csp = player._response_headers(upstream, html_frame=True, origin=origin)["Content-Security-Policy"]
        self.assertIn("connect-src 'self' https://play.example.test data:", csp)
        self.assertIn("script-src 'self' https://play.example.test 'unsafe-inline'", csp)
        self.assertEqual(player._site_origin(req(b"evil.test; script-src *")), "")
        self.assertIn("connect-src 'self' data:", player._response_headers(upstream, html_frame=True)["Content-Security-Policy"])

    def test_alloha_icon_sprite_is_fetched_from_its_own_host(self):
        # A same-origin sprite stays <use> links, which the opaque sandbox refuses to load.
        js = "var t=['no-cors','/images/allplay.svg','isVersionAtLeast'];"
        rewritten = player._rewrite_javascript(js, 'https://alloha.yani.tv/build/app.f950612f.js')
        self.assertIn("'https://alloha.yani.tv/images/allplay.svg'", rewritten)
        self.assertEqual(player._rewrite_javascript(js, 'https://cdn.example.test/app.js'), js)

    def test_nested_playlists_keep_the_page_referer(self):
        page = 'https://alloha.yani.tv/?token_movie=abc'
        master = 'https://cdn.vkvideo.cloud/0/abc/master.m3u8'
        text = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n720/index.m3u8\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
        rendered = player._rewrite_m3u8(text, master, page)
        self.assertIn(player._proxy_url('https://cdn.vkvideo.cloud/0/abc/720/index.m3u8', page), rendered)
        self.assertIn(player._proxy_url('https://cdn.vkvideo.cloud/0/abc/key.bin', page), rendered)
        self.assertNotIn('base=' + quote(master, safe=''), rendered)

    async def test_clean_players_keep_their_specialized_implementation(self):
        url = 'https://ru.yummyani.me/iframeCVH.html?anime_id=123'
        req = self.request()
        with patch.object(player, '_cvh_frame', AsyncMock(return_value=Response('clean'))) as clean:
            result = await player.frame(req, url)
            self.assertEqual(result.status_code, 200)
            clean.assert_awaited_once_with(req, url)

    async def test_query_proxy_html_redirect_closes_upstream_stream(self):
        url = 'https://player.aksor.tv/embed?vid=123'
        body = Body(b'<html></html>')
        client = mock_client(lambda req: httpx.Response(200, headers={'content-type': 'text/html'}, stream=body))
        with patch.object(player, '_get_client', AsyncMock(return_value=client)), \
             patch.object(player.adblock, 'should_block', return_value=(False, '')):
            result = await player._fetch(self.request(), url, BASE)
        self.assertEqual(result.status_code, 307)
        self.assertEqual(result.headers['location'], '/player/documents/https/player.aksor.tv/embed?vid=123')
        self.assertTrue(body.closed)

    async def test_playlists_reach_safari_as_hls(self):
        # Kodik's CDN sends text/plain; with no .m3u8 in /player/proxy, iOS refused the stream.
        url = 'https://p13.solodcdn.com/s/m/abc/360.mp4:hls:manifest.m3u8'
        client = mock_client(lambda req: httpx.Response(
            200, headers={'content-type': 'text/plain; charset=utf-8'},
            content=b'#EXTM3U\n#EXTINF:6.000,\n360.mp4:hls:seg-1-v1-a1.ts\n'))
        with patch.object(player, '_get_client', AsyncMock(return_value=client)), \
             patch.object(player.adblock, 'should_block', return_value=(False, '')):
            result = await player._fetch(self.request(), url, 'https://kodikplayer.com/season/1/abc/720p')
        self.assertEqual(result.headers['content-type'], 'application/vnd.apple.mpegurl')
        self.assertEqual(sum(1 for k, _ in result.raw_headers if k.lower() == b'content-type'), 1)
        self.assertIn(b'/player/proxy?url=https%3A%2F%2Fp13.solodcdn.com%2Fs%2Fm%2Fabc%2F360.mp4%3Ahls%3Aseg-1', result.body)

    async def test_document_response_retains_csp(self):
        url = PLAYER
        client = mock_client(lambda req: httpx.Response(
            200, headers={'content-type': 'text/html'}, content=b'<head></head><body>test</body>'))
        with patch.object(player, '_get_client', AsyncMock(return_value=client)), \
             patch.object(player.adblock, 'should_block', return_value=(False, '')):
            result = await player._fetch(self.request(), url, url, frame=True)
        self.assertEqual(result.status_code, 200)
        self.assertIn('Content-Security-Policy', result.headers)
        self.assertIn('data-asoplay-bridge', result.body.decode())


if __name__ == '__main__':
    unittest.main()
