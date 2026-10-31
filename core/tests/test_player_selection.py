"""
Выбор плеера: VK — крайний вариант и играет только через чистый фрейм.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import itertools
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request
from core.api.sections.sources import media
from core.functions import native_sources, source_filters
from core.functions import sources as source_registry  # у проверки есть своя переменная sources
from core.modules.player import routes as player_proxy
from core.functions.player_hosts import is_disabled_player_url, iframe_preference_score, vk_video_ref

VK = 'https://ru.yummyani.me/iframeVK.html?id=-1_2'
VK_BROKEN = 'https://vk.com/video_ext.php?id=1'
CVH = 'https://ru.yummyani.me/iframeCVH.html?anime_id=123&episode=1'
KODIK = 'https://kodikplayer.com/serial/123/abc/720p'


def request(path='/player/frame'):
    return Request({'type': 'http', 'method': 'GET', 'headers': [], 'path': path})


class Selection(unittest.IsolatedAsyncioTestCase):
    async def generic_dubs(self, entries):
        sources = [SimpleNamespace(title=name, url=url) for name, url in entries]
        ep = SimpleNamespace(a_get_sources=AsyncMock(return_value=sources))
        with patch.object(source_registry, 'get', return_value=ep), \
             patch.object(source_registry, 'put', side_effect=lambda s: s.url):
            return await media.dubs_for_episode('episode', 'yummy_anime')

    async def native_dubs(self, entries):
        ep = {'source': 'oldyummy', 'dubs': [
            {'data': {'dubbing': name}, 'iframe_url': url} for name, url in entries
        ]}
        with patch.object(source_registry, 'get', return_value=ep), \
             patch.object(source_registry, 'put', side_effect=lambda s: s['iframe_url']):
            return await native_sources.native_dubs('oldyummy', 'episode')

    async def test_cvh_wins_on_both_paths_regardless_of_input_order(self):
        for urls in itertools.permutations([VK, CVH, KODIK]):
            entries = [('Dub', url) for url in urls]
            for load in (self.generic_dubs, self.native_dubs):
                dubs = await load(entries)
                self.assertEqual([d['key'] for d in dubs], [CVH])

    async def test_vk_only_dub_stays_available(self):
        for load in (self.generic_dubs, self.native_dubs):
            self.assertEqual([d['key'] for d in await load([('Dub', VK)])], [VK])

    async def test_vk_dubs_follow_every_other_player_in_original_order(self):
        entries = [('Alpha', VK), ('Beta', KODIK), ('Gamma', VK + '1'), ('Delta', CVH)]
        for load in (self.generic_dubs, self.native_dubs):
            names = [d['name'] for d in await load(entries)]
            self.assertEqual(names, ['Beta', 'Delta', 'Alpha', 'Gamma'])

    async def test_same_named_dub_prefers_any_other_player_to_vk(self):
        for load in (self.generic_dubs, self.native_dubs):
            self.assertEqual([d['key'] for d in await load([('Dub', VK), ('Dub', KODIK)])], [KODIK])

    async def test_unparseable_vk_url_is_dropped(self):
        for load in (self.generic_dubs, self.native_dubs):
            self.assertEqual(await load([('Dub', VK_BROKEN)]), [])

    async def test_subtitles_and_other_dubs_remain_separate(self):
        entries = [('Озвучка AliveDub', CVH), ('Субтитры AliveDub', KODIK), ('Other', KODIK)]
        for load in (self.generic_dubs, self.native_dubs):
            dubs = await load(entries)
            self.assertEqual(len(dubs), 3)
            self.assertEqual([d['kind'] for d in dubs], ['voice', 'sub', 'voice'])

    async def test_vk_source_key_returns_the_wrapper_for_the_clean_frame(self):
        source = SimpleNamespace(url=VK, title='AliveDub', get_videos=Mock())
        with patch.object(source_registry, 'get', return_value=source), \
             patch.object(source_filters.adblock, 'filter_videos', AsyncMock(side_effect=lambda v: v)):
            videos = await media.videos('key', 'yummy_anime')
        self.assertEqual([(v['url'], v['type']) for v in videos], [(VK, 'iframe')])
        source.get_videos.assert_not_called()

    async def test_unparseable_vk_source_key_never_extracts_videos(self):
        source = SimpleNamespace(url=VK_BROKEN, get_videos=Mock())
        with patch.object(source_registry, 'get', return_value=source):
            self.assertEqual(await media.videos('stale', 'yummy_anime'), [])
        source.get_videos.assert_not_called()

    async def test_video_filter_keeps_vk_refs_and_drops_raw_vk_pages(self):
        keep = [{'url': VK, 'type': 'iframe'}, {'url': KODIK, 'type': 'iframe'}]
        with patch.object(source_filters.adblock, 'filter_videos', AsyncMock(side_effect=lambda v: v)) as filtered:
            self.assertEqual(await source_filters.filter_source_videos([*keep, {'url': VK_BROKEN}]), keep)
        filtered.assert_awaited_once_with(keep)


class Policy(unittest.TestCase):
    def test_vk_refs_from_wrapper_and_embed(self):
        cases = {
            VK: ('-1', '2', ''),
            '//ru.yummyani.me/iframeVK.html?id=video-238212834_456239044': ('-238212834', '456239044', ''),
            'https://vk.com/video_ext.php?oid=-5&id=7&hash=ab12': ('-5', '7', 'ab12'),
            'https://vkvideo.ru/video_ext.php?oid=5&id=7&hash=<x>': ('5', '7', ''),
        }
        for url, (oid, vid, embed_hash) in cases.items():
            self.assertEqual(vk_video_ref(url), {'oid': oid, 'id': vid, 'hash': embed_hash}, url)
        for url in (VK_BROKEN, 'https://ru.yummyani.me/iframeVK.html?id=2', 'https://ru.yummyani.me/iframeVK.html?id=0_2',
                    'https://ru.yummyani.me/iframeVK.html?id=-1_x', 'https://example.test/video_ext.php?oid=1&id=2',
                    CVH, KODIK, ''):
            self.assertIsNone(vk_video_ref(url), url)

    def test_vk_hosts_and_wrapper_never_load_as_documents(self):
        for url in (VK, '//ru.yummyani.me/iframeVK.html?id=2', VK_BROKEN,
                    'https://m.vk.ru/video_embed.php', 'https://vkvideo.ru/video_ext.php',
                    'https://vkvideo.com/video_ext.php', 'https://vk.com./video_ext.php'):
            self.assertTrue(is_disabled_player_url(url), url)
        for url in (CVH, KODIK, 'https://video.sibnet.ru/shell.php?videoid=1',
                    'https://vk.com.example.test/embed', 'https://okcdn.ru/stream.mp4',
                    'https://example.test/embed?title=vk.com', ''):
            self.assertFalse(is_disabled_player_url(url), url)

    def test_preference_ranks_vk_below_unknown_players(self):
        self.assertGreater(iframe_preference_score(CVH), iframe_preference_score(KODIK))
        self.assertEqual(iframe_preference_score('https://example.test/?q=kodikplayer.com'), 0)
        self.assertLess(iframe_preference_score(VK), iframe_preference_score('https://example.test/embed'))


EMBED = '<script>var p = {"duration":65,"hls_ondemand":"https:\\/\\/vkvd1.okcdn.ru\\/srcIp\\/1\\/master.m3u8?x=1\\u0026y=2"};</script>'
MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=500,RESOLUTION=640x360
low/track.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900,RESOLUTION=1280x720
https://vkvd2.okcdn.ru/high/track.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=901,RESOLUTION=1280x720
https://vkvd2.okcdn.ru/high-duplicate/track.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=902,RESOLUTION=1920x1080
https://evil.example.test/track.m3u8
"""


def response(text, status=200, url='https://vk.com/video_ext.php'):
    return httpx.Response(status, text=text, request=httpx.Request('GET', url))


class VkFrame(unittest.IsolatedAsyncioTestCase):
    async def render(self, *responses):
        client = Mock(get=AsyncMock(side_effect=list(responses)))
        with patch.object(player_proxy, '_get_client', AsyncMock(return_value=client)):
            result = await player_proxy.frame(request(), VK)
        return result.body.decode(), client

    async def test_frame_routes_vk_to_clean_player_with_qualities(self):
        body, client = await self.render(response(EMBED), response(MASTER))
        embed_call = client.get.await_args_list[0]
        self.assertEqual(embed_call.args[0], 'https://vk.com/video_ext.php')
        self.assertEqual(embed_call.kwargs['params'], {'oid': '-1', 'id': '2', 'hd': '2'})
        self.assertEqual(client.get.await_args_list[1].args[0], 'https://vkvd1.okcdn.ru/srcIp/1/master.m3u8?x=1&y=2')
        self.assertIn('id="av-vk-video"', body)
        self.assertIn('"label":"Auto"', body)
        self.assertLess(body.index('"label":"720p"'), body.index('"label":"360p"'))
        self.assertIn('/player/proxy?url=https%3A%2F%2Fvkvd1.okcdn.ru%2FsrcIp%2F1%2Fmaster.m3u8', body)
        self.assertNotIn('high-duplicate', body)
        self.assertNotIn('evil.example.test', body)
        self.assertIn('if (true && !video.canPlayType', body)

    async def test_master_failure_still_plays_auto(self):
        body, _ = await self.render(response(EMBED), httpx.ConnectError('down'))
        self.assertIn('"label":"Auto"', body)
        self.assertNotIn('"label":"720p"', body)

    async def test_unavailable_or_foreign_stream_shows_an_honest_frame(self):
        foreign = EMBED.replace('vkvd1.okcdn.ru', 'evil.example.test')
        for embed in ('<html>video removed</html>', foreign):
            body, client = await self.render(response(embed))
            self.assertIn('Видео недоступно', body)
            self.assertEqual(client.get.await_count, 1)

    async def test_embed_failure_shows_load_error(self):
        body, _ = await self.render(response('', status=500))
        self.assertIn('Видео не загрузилось', body)

    async def test_raw_vk_documents_never_load(self):
        with patch.object(player_proxy, '_get_client', AsyncMock()) as client:
            for call in (lambda: player_proxy.frame(request(), VK_BROKEN),
                         lambda: player_proxy._fetch(request(), VK, VK),
                         lambda: player_proxy._fetch(request(), VK_BROKEN, VK_BROKEN)):
                with self.assertRaises(HTTPException) as caught:
                    await call()
                self.assertEqual(caught.exception.status_code, 403)
            client.assert_not_awaited()

    def test_mp4_clean_frames_do_not_require_native_hls(self):
        body = player_proxy._clean_video_frame('https://vkvd1.okcdn.ru/video.mp4', 'https://vk.com/').body.decode()
        self.assertIn('if (false && !video.canPlayType', body)


if __name__ == '__main__':
    unittest.main()
