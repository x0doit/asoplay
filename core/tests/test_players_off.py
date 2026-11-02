"""
Выключенные виды плееров (site_settings.players_off): вид по адресу, их озвучек нет в выборе, их кадры и запросы
прокси — 403; пустой список включает всё обратно, VK живёт по своим правилам. Без сети и без базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from core.functions import player_hosts, site_settings, source_filters
from core.modules.player import routes

ALLOHA = "https://alloha.yani.tv/?token_movie=70e925553b741b4ba4b06cf78ee994&translation=79&season=1&episode=1"


class PlayersOff(unittest.TestCase):
    def switched_off(self, *kinds):
        state = site_settings._State()
        state.settings = site_settings.Settings(players_off=tuple(kinds))
        p = patch.object(site_settings, "state", state)
        p.start()
        self.addCleanup(p.stop)

    def test_the_kind_comes_from_the_host_or_the_path(self):
        kinds = {url: player_hosts.player_kind(url) for url in (
            ALLOHA, "https://r504.vkvideo.cloud/0/x/index.m3u8", "https://kodikplayer.com/serial/7963/abc/720p",
            "https://ru.yummyani.me/iframeCVH.html?anime_id=1", "https://video.sibnet.ru/shell.php?videoid=1",
            "https://vk.com/video_ext.php?oid=-1&id=2", "https://example.com/player")}
        self.assertEqual(list(kinds.values()), ["alloha", "alloha", "kodik", "cvh", "sibnet", "vk", None])

    def test_by_default_alloha_is_off_and_left_out_of_the_dubs(self):
        self.assertEqual(site_settings.Settings().players_off, ("alloha",))
        self.switched_off("alloha")
        self.assertTrue(source_filters.unplayable_player_url(ALLOHA))
        self.assertFalse(source_filters.unplayable_player_url("https://kodikplayer.com/serial/7963/abc/720p"))

    def test_an_empty_list_brings_every_player_back(self):
        self.switched_off()
        self.assertFalse(source_filters.unplayable_player_url(ALLOHA))
        self.assertFalse(source_filters.unplayable_player_url("https://vk.com/video_ext.php?oid=-1&id=2"))
        self.assertTrue(source_filters.unplayable_player_url("https://vk.com/clip-1_2"))  # VK — свои правила

    def test_the_frame_and_the_proxy_refuse_a_switched_off_player(self):
        self.switched_off("alloha")
        for call in (routes.frame(None, ALLOHA), routes._fetch(None, "https://r504.vkvideo.cloud/0/x/seg1.ts", ALLOHA)):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(call)
            self.assertEqual((caught.exception.status_code, caught.exception.detail), (403, "player disabled"))


if __name__ == "__main__":
    unittest.main()
