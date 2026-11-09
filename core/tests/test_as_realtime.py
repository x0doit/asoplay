"""
Связь хаба AsoPlay с realtime соцсети (сервер к серверу): что хаб отправляет и как живёт без связи.
Саму связь проверяют вживую с realtime-сервером соцсети, здесь — без сети. Живую базу не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import concurrent.futures
import json
import time
import unittest
from unittest.mock import patch

import websockets

from core.classes import as_api, as_realtime
from core.config.config import SITE_URL
from core.functions import presence, presence_batch, privacy as privacy_rules, realtime, realtime_social, sessions


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



class _RealtimeWS:
    """Поддельный realtime: отдаёт свои кадры, потом обрыв без закрывающего кадра (как 02.11 на проде) или живёт."""

    def __init__(self, frames, alive):
        self.frames, self.alive, self.sent = list(frames), alive, []

    async def send(self, data):
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.01)
        if self.frames:
            return json.dumps(self.frames.pop(0))
        if self.alive:
            await asyncio.Event().wait()
        raise websockets.ConnectionClosedError(None, None)


class _Connect:
    """connect для SocialLink: сессии по очереди — (кадры, жива ли); время каждого подключения — в opened."""

    def __init__(self, *sessions):
        self.sessions, self.opened = list(sessions), []

    def __call__(self, url, **_):
        frames, alive = self.sessions.pop(0) if self.sessions else ([{"type": "ready"}], True)
        opened, ws = self.opened, _RealtimeWS(frames, alive)

        class Context:
            async def __aenter__(self):
                opened.append(time.monotonic())
                return ws

            async def __aexit__(self, *exc):
                return False
        return Context()


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    """Переподключение после обрыва: срок попытки, пауза, адрес в своём потоке и с памятью."""

    async def asyncSetUp(self):
        self.config_calls = []
        self.config_delay = 0.0
        for p in (patch.object(as_realtime.as_sessions, "enabled", return_value=True),
                  patch.object(as_realtime.config, "as_client_keys", return_value=("c", "s")),
                  patch.object(as_realtime.as_api, "call", side_effect=self.system_config),
                  patch.object(as_realtime, "FIRST_RETRY", 0.05), patch.object(as_realtime, "BACKOFF_MAX", 0.2),
                  patch.object(as_realtime, "_tls", return_value=None)):  # поддельному realtime TLS не нужен
            p.start()
            self.addCleanup(p.stop)

    def system_config(self, method, path):
        self.config_calls.append(path)
        time.sleep(self.config_delay)
        return {"realtime": {"url": "wss://realtime.test/app"}}

    def link(self, connect):
        async def snapshot():
            return {"users": []}

        async def on_event(*_):
            return None
        link = as_realtime.SocialLink(snapshot, on_event, connect=connect)
        self.addAsyncCleanup(link.stop)
        return link

    async def until(self, cond, wait=2.0):
        deadline = time.monotonic() + wait
        while not cond() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        return cond()

    async def test_an_abrupt_close_connects_again_at_once_and_reuses_the_address(self):
        connect = _Connect(([{"type": "ready"}], False), ([{"type": "ready"}], True))
        link = self.link(connect)
        link.start()
        self.assertTrue(await self.until(lambda: len(connect.opened) == 2 and link.ready))
        self.assertLess(connect.opened[1] - connect.opened[0], 0.5)
        self.assertEqual(self.config_calls, ["/system/config"])  # адрес запомнен: /system/config — один раз

    async def test_a_hanging_config_request_does_not_hold_the_attempt(self):
        self.config_delay = 0.6
        connect = _Connect()
        link = self.link(connect)
        with patch.object(as_realtime, "ATTEMPT_TIMEOUT", 0.2), \
                self.assertLogs("asoplay.as_realtime", "WARNING") as logs:
            link.start()
            self.assertTrue(await self.until(lambda: any("no ready within" in m for m in logs.output)))
            self.config_delay = 0.0
            self.assertTrue(await self.until(lambda: link.ready, wait=3.0))

    async def test_a_busy_shared_thread_pool_does_not_stop_reconnecting(self):
        loop = asyncio.get_running_loop()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown, wait=False)
        loop.set_default_executor(pool)
        busy = loop.run_in_executor(None, time.sleep, 1.5)  # общий пул занят чужой работой
        connect = _Connect()
        link = self.link(connect)
        link.start()
        self.assertTrue(await self.until(lambda: link.ready, wait=0.8))
        await busy

    async def test_the_address_is_read_again_after_several_failures(self):
        link = self.link(_Connect())
        self.assertEqual(await link._realtime_url(0), "wss://realtime.test/app")
        link.url = "wss://realtime.test/app"
        await link._realtime_url(as_realtime.URL_STALE_AFTER - 1)
        self.assertEqual(len(self.config_calls), 1)
        await link._realtime_url(as_realtime.URL_STALE_AFTER)
        self.assertEqual(len(self.config_calls), 2)

    async def test_a_link_down_for_a_minute_is_an_error_once_a_minute(self):
        link = self.link(_Connect())
        link._down_since = time.monotonic() - as_realtime.DOWN_ERROR_AFTER - 1
        with self.assertLogs("asoplay.as_realtime", "ERROR") as logs:
            link._log_if_down()
            link._log_if_down()
        self.assertEqual(len(logs.output), 1)


if __name__ == "__main__":
    unittest.main()
