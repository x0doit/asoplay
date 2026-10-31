"""
Связь хаба AsoPlay с realtime соцсети (сервер к серверу): что хаб отправляет и как живёт без связи.
Саму связь проверяют вживую с realtime-сервером соцсети, здесь — без сети. Живую базу не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import json
import unittest
from unittest.mock import patch

from core.classes import as_api, as_realtime
from core.functions import presence, presence_batch, privacy as privacy_rules, realtime, realtime_social, sessions
from core.modules.title_pages import SITE_URL


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = None
        self.headers = {"user-agent": "Firefox"}

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000):
        self.closed = code


class SocialLinkTests(unittest.TestCase):
    """Само соединение проверяется вживую с realtime-сервером соцсети (node core/config/server/serverJust.js),
    без подделки сервера."""

    def test_nothing_is_queued_without_a_connection(self):
        link = as_realtime.SocialLink(None, None)
        link.presence(17, True, None)
        self.assertTrue(link._queue.empty())


class HubSocialTests(unittest.TestCase):
    """Наруто (2) на AsoPlay, Сакура (1) — его друг."""

    def setUp(self):
        self.clock = FakeClock()
        self.store = presence.Presence(grace=30, clock=self.clock)
        self.hub = realtime.Hub(clock=self.clock)
        self.sent_to_social = []
        self.hub.social.presence = lambda uid, online, watching: self.sent_to_social.append((uid, online, watching))
        self.privacy = {1: privacy_rules.Privacy(show_watching=True)}
        self.revoked = []
        patches = [
            patch.object(presence, "presence", self.store),
            patch.object(privacy_rules, "for_users", side_effect=lambda ids: {u: self.privacy[u] for u in ids if u in self.privacy}),
            patch.object(presence, "title_cards", side_effect=lambda ids: {52991: ("Фрирен", "")} if 52991 in ids else {}),
            patch.object(sessions, "revoke_user_sessions", side_effect=self.revoked.append),
            # Hub без core.py: состояние сервера связываем явно, как core.py
            patch.object(realtime_social, "status_provider", lambda: {"ok": True, "sources": [], "vpn": False}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def connect(self, uid, friends=()):
        ws = FakeWS()
        conn = self.hub.join(ws, {"id": uid, "name": f"user{uid}", "avatar": None, "profile_path": f"/@u{uid}"}, f"s{uid}", "198.51.100.7")
        self.hub.set_friends(uid, frozenset(friends))
        return conn, ws

    def test_changes_go_to_the_social_network_with_the_watching_link(self):
        sakura, _ = self.connect(1)
        asyncio.run(self.hub.handle(sakura, json.dumps({"type": "visible", "value": True})))
        asyncio.run(self.hub.handle(sakura, json.dumps({"type": "watching", "mal_id": 52991, "title": "из вкладки"})))
        asyncio.run(self.hub.leave(sakura))
        self.clock.t += 31
        asyncio.run(self.hub.sweep())
        self.assertEqual(self.sent_to_social, [
            (1, True, None),
            (1, True, {"title": "Фрирен", "url": f"{SITE_URL}/anime/52991"}),
            (1, False, None),
        ])

    def test_watching_switched_on_mid_play_reaches_the_social_network_at_once(self):
        self.privacy[1] = privacy_rules.Privacy()                             # по умолчанию «что смотрю» скрыто
        sakura, _ = self.connect(1)
        asyncio.run(self.hub.handle(sakura, json.dumps({"type": "visible", "value": True})))
        asyncio.run(self.hub.handle(sakura, json.dumps({"type": "watching", "mal_id": 52991, "title": "из вкладки"})))
        self.assertEqual(self.sent_to_social, [(1, True, None)])
        self.privacy[1] = privacy_rules.Privacy(show_watching=True)
        asyncio.run(self.hub.privacy_changed(1))
        self.assertEqual(self.sent_to_social[-1], (1, True, {"title": "Фрирен", "url": f"{SITE_URL}/anime/52991"}))

    def test_a_friend_in_the_social_network_reaches_the_tabs_it_is_meant_for(self):
        _, naruto_ws = self.connect(2, friends={1})
        _, hinata_ws = self.connect(3)
        asyncio.run(self.hub.on_social_event("presence", {"user_id": 1, "online": True, "online_where": "site", "last_seen": 5}, [2]))
        self.assertEqual(naruto_ws.sent, [{"type": "social_presence", "user_id": 1, "online_where": "site", "last_seen": 5}])
        self.assertEqual(hinata_ws.sent, [])

    def test_friendship_changes_update_who_hears_whom(self):
        _, naruto_ws = self.connect(2)
        asyncio.run(self.hub.on_social_event("profile_friend", {"user_id": 1, "status": "friends"}, [2]))
        self.assertEqual(self.hub.followers.get(1), {2})
        asyncio.run(self.hub.on_social_event("profile_friend", {"user_id": 1, "status": "none"}, [2]))
        self.assertNotIn(1, self.hub.followers)
        self.assertEqual([f["status"] for f in naruto_ws.sent], ["friends", "none"])
        asyncio.run(self.hub.on_social_event("profile_friend", {"user_id": 1, "status": "hacked"}, [2]))
        self.assertEqual(len(naruto_ws.sent), 2)

    def test_a_ban_closes_every_tab_and_session_of_that_person(self):
        _, ws_a = self.connect(1)
        _, ws_b = self.connect(1)
        _, other = self.connect(2)
        asyncio.run(self.hub.on_social_event("account_banned", {"user_id": 1}, []))
        self.assertEqual(self.revoked, [1])
        self.assertEqual((ws_a.closed, ws_b.closed, other.closed), (4001, 4001, None))

    def test_the_snapshot_is_the_batch_body(self):
        with patch.object(presence_batch, "build_batch", return_value={"users": [1], "watching": []}):
            self.assertEqual(asyncio.run(self.hub.social_snapshot()), {"users": [1], "watching": []})

    def test_the_minute_batch_waits_while_the_link_is_up(self):
        with patch.object(realtime.hub.social, "connected", True):
            self.assertFalse(presence_batch.batch_needed())
        with patch.object(realtime.hub.social, "connected", False):
            self.assertTrue(presence_batch.batch_needed())


if __name__ == "__main__":
    unittest.main()
