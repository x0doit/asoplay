"""
Серии yummy_anime поштучно (core/functions/source_episodes.py): дробная серия у источника не роняет весь список.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Образец — по ответу api.yani.tv /anime/1512/videos («Ван-Пис») от 2 ноября 2026: серии 1167–1169 и между ними одна
запись "1168.5" (AniStar, длительность 0), которую anicli-api 0.8.6 не может превратить в int — и терял весь список.
Плюс видео плеера alloha: его библиотека пропускает, адаптер тоже.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from anicli_api.source.yummy_anime import Anime, Episode

from core.api.sections.sources import search
from core.functions import source_episodes, source_health
from core.functions import sources as source_registry


def video(video_id, number, dubbing, iframe, duration=1400):
    """Запись списка видео в той же форме, что у api.yani.tv."""
    return {"video_id": video_id, "number": number, "data": {"player": "Плеер CVH", "dubbing": dubbing, "player_id": 3},
            "author": {"avatars": {}, "roles": []}, "date": 1789132491, "iframe_url": iframe, "index": video_id,
            "skips": {"opening": None, "ending": None}, "views": 1 if duration else 0, "duration": duration}


CVH = "//ru.yummyani.me/iframeCVH.html?dubbing_code={d}&anime_id=21&episode={n}"
SAMPLE = [
    video(1309936, "1167", "Озвучка AnimeVost", CVH.format(d="AnimeVost", n="1167")),
    video(1309937, "1167", "Озвучка AniStar", CVH.format(d="AniStar", n="1167")),
    video(1309938, "1167", "Озвучка Alloha", "//alloha.yani.tv/?translation=79&season=1&episode=1167"),
    video(1309940, "1168", "Озвучка AnimeVost", CVH.format(d="AnimeVost", n="1168")),
    video(1309942, "1168.5", "Озвучка AniStar", CVH.format(d="AniStar", n="1168.5"), duration=0),
    video(1309944, "1169", "Озвучка AnimeVost", CVH.format(d="AnimeVost", n="1169")),
]


def anime(videos, anime_id=1512, ok=True):
    """Тайтл классом библиотеки, как его отдаёт a_get_anime, с API-клиентом, который отвечает списком видео."""
    answer = SimpleNamespace(success=ok, data={"response": videos} if ok else {}, status_code=200 if ok else 0,
                             error=None if ok else RuntimeError("Request Error: connection reset"))
    api = SimpleNamespace(get_anime_videos=AsyncMock(return_value=answer))
    return Anime(title="Ван-Пис", thumbnail="", description="", data={"anime_id": anime_id},
                 sync_api=api, async_api=api, http=object(), http_async=object())


class YummyEpisodes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source_episodes._warned.clear()

    async def test_the_library_itself_still_loses_the_whole_list(self):
        # Канарейка: anicli-api 0.8.6 на этом образце падает целиком — ради этого и есть адаптер. Упала проверка
        # после обновления библиотеки — авторы исправили разбор номера, адаптер можно пересмотреть
        with self.assertRaises(ValueError):
            await anime(SAMPLE).a_get_episodes()

    async def test_a_fractional_episode_is_skipped_and_the_rest_of_the_list_stays(self):
        title = anime(SAMPLE)
        episodes = await source_episodes.yummy_anime(title)
        self.assertEqual([e.ordinal for e in episodes], [1167, 1168, 1169])
        self.assertTrue(all(isinstance(e, Episode) for e in episodes))
        self.assertEqual([v["video_id"] for v in episodes[0].data], [1309936, 1309937])  # alloha пропущен
        self.assertIs(episodes[0].async_api, title.async_api)  # озвучки серии пойдут тем же клиентом
        title.async_api.get_anime_videos.assert_awaited_once_with(id=1512)

    async def test_one_warning_per_title_with_the_raw_value(self):
        with self.assertLogs("asoplay.sources", "WARNING") as logs:
            await source_episodes.yummy_anime(anime(SAMPLE))
        self.assertEqual(len(logs.records), 1)
        self.assertIn("anime 1512", logs.output[0])
        self.assertIn("'1168.5'", logs.output[0])
        with self.assertNoLogs("asoplay.sources", "WARNING"):
            await source_episodes.yummy_anime(anime(SAMPLE))  # следующий зритель — журнал молчит
        with self.assertLogs("asoplay.sources", "WARNING") as other:
            await source_episodes.yummy_anime(anime([video(1, "12.5", "Озвучка X", CVH.format(d="X", n="12.5"))], anime_id=7))
        self.assertEqual(len(other.records), 1)  # другой тайтл — своё предупреждение
        self.assertIn("anime 7", other.output[0])

    async def test_whole_numbers_make_no_warning(self):
        with self.assertNoLogs("asoplay.sources", "WARNING"):
            episodes = await source_episodes.yummy_anime(anime([v for v in SAMPLE if v["number"] != "1168.5"]))
        self.assertEqual([e.ordinal for e in episodes], [1167, 1168, 1169])

    def test_numbers_are_read_like_the_library_but_fractional_is_none(self):
        for raw, num in (("12", 12), (12, 12), (12.0, 12), (" 7 ", 7), ("0", 0),
                         ("1168.5", None), (1168.5, None), (None, None), ("", None), ("ova", None)):
            self.assertEqual(source_episodes.episode_number(raw), num, raw)

    async def test_a_failed_request_raises_the_client_error_as_before(self):
        with self.assertRaisesRegex(RuntimeError, "Request Error"):
            await source_episodes.yummy_anime(anime([], ok=False))

    async def test_the_episodes_section_answers_the_list_instead_of_502(self):
        found = SimpleNamespace(a_get_anime=AsyncMock(return_value=anime(SAMPLE)))
        with patch.object(source_registry, "get", return_value=found), \
             patch.object(source_registry, "put", side_effect=lambda e: f"ep{e.ordinal}"):
            out = await search.episodes(key="title", source="yummy_anime")
        self.assertEqual(out, [{"num": "1167", "title": "Episode", "key": "ep1167"},
                               {"num": "1168", "title": "Episode", "key": "ep1168"},
                               {"num": "1169", "title": "Episode", "key": "ep1169"}])
        self.assertEqual(source_health._state("yummy_anime").consecutive_failures, 0)  # источнику не засчитан сбой


if __name__ == "__main__":
    unittest.main()
