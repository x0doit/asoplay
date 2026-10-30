"""
Кто сейчас на AsoPlay: видимые вкладки, 30 секунд после ухода, что смотрит; пакет для соцсети
по приватности.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/presence: вход (core/tests/test_presence.py) собирает части по порядку.
"""
import unittest
from unittest.mock import patch

from core.classes import as_api
from core.functions import presence

from core.tests.presence.lib import SITE, FakeClock


class PresenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = presence.Presence(grace=30, clock=self.clock)

    def test_a_visible_tab_keeps_the_person_here_and_the_grace_survives_a_reload(self):
        self.store.tab(5, 1, True, 52991, "Фрирен")
        self.assertEqual(self.store.current()[5].mal_id, 52991)
        self.store.close(5, 1)
        self.clock.t += 29
        self.assertEqual(self.store.current()[5].mal_id, 52991)     # ещё 30 секунд — с тем, что смотрел
        self.clock.t += 2
        self.assertEqual(self.store.current(), {})

    def test_a_hidden_tab_does_not_count_and_coming_back_cancels_the_grace(self):
        self.store.tab(5, 1, False)
        self.assertEqual(self.store.current(), {})
        self.store.tab(5, 1, True)
        self.store.tab(5, 1, False)
        self.clock.t += 20
        self.assertIn(5, self.store.current())
        self.store.tab(5, 1, True, 1, "Ковбой Бибоп")
        self.clock.t += 100
        self.assertEqual(self.store.current()[5].mal_id, 1)

    def test_the_tab_where_the_player_started_last_wins(self):
        self.store.tab(5, 1, True, 1, "Ковбой Бибоп")
        self.clock.t += 1
        self.store.tab(5, 2, True, 52991, "Фрирен")
        self.assertEqual(self.store.current()[5].mal_id, 52991)
        self.store.tab(5, 2, False, 52991, "Фрирен")                # вкладку с Фрирен свернули
        self.assertEqual(self.store.current()[5].mal_id, 1)
        self.store.close(5, 1)
        self.assertEqual(self.store.current()[5].mal_id, 1)         # пауза — с последним, что было на экране

    def test_titles_are_cleaned_and_cut(self):
        cleaned = presence.clean_title("  Фрирен\n\u200bпровожающая\t\ufeff" + "я" * 200)
        self.assertTrue(cleaned.startswith("Фрирен провожающая я"))
        self.assertEqual(len(cleaned), 100)
        self.assertEqual(presence.clean_title(None), "")


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.store = presence.Presence(clock=FakeClock())
        self.privacy = {}
        self.titles = {52991: ("Фрирен, провожающая в последний путь", "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg")}
        self.calls = []
        patches = [
            patch.object(presence, "presence", self.store),
            patch.object(presence, "_privacy", side_effect=lambda ids: {u: self.privacy[u] for u in ids if u in self.privacy}),
            patch.object(presence, "_titles", side_effect=lambda ids: {m: self.titles[m] for m in ids if m in self.titles}),
            patch.object(as_api, "call", side_effect=lambda *a, **kw: self.calls.append((a, kw))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_privacy_decides_who_is_sent_and_what_they_watch(self):
        self.store.tab(1, 1, True, 52991, "из браузера")                     # по умолчанию: на сайте, без «смотрит»
        self.store.tab(2, 1, True, 52991, "Фрирен")
        self.privacy[2] = presence.Privacy(hide_presence=True, show_watching=True)
        self.store.tab(3, 1, True, 52991, "из браузера")
        self.privacy[3] = presence.Privacy(show_watching=True)
        self.store.tab(4, 1, True, 1, "Ковбой Бибоп")                        # тайтла нет в кеше — название из браузера
        self.privacy[4] = presence.Privacy(show_watching=True)
        self.store.tab(6, 1, True)                                           # ничего не играет
        self.privacy[6] = presence.Privacy(show_watching=True)
        batch = presence.build_batch()
        self.assertEqual(batch["users"], [1, 3, 4, 6])
        self.assertEqual(batch["watching"], [
            {"user_id": 3, "title": "Фрирен, провожающая в последний путь", "url": f"{SITE}/anime/52991"},
            {"user_id": 4, "title": "Ковбой Бибоп", "url": f"{SITE}/anime/1"},
        ])

    def test_an_empty_batch_goes_once_after_someone_left(self):
        self.assertFalse(presence.report_once(False))
        self.assertEqual(self.calls, [])
        self.store.tab(1, 1, True)
        self.assertTrue(presence.report_once(False))
        self.store = presence.Presence(clock=FakeClock())
        with patch.object(presence, "presence", self.store):
            self.assertFalse(presence.report_once(True))
            self.assertFalse(presence.report_once(False))
        self.assertEqual([kw["body"]["users"] for _, kw in self.calls], [[1], []])
        self.assertEqual(self.calls[0][0], ("POST", "/clients/presence"))
