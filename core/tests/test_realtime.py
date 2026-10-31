"""
Живое соединение (/ws): присутствие для друзей, приватность, пауза после ухода, статус сервера, лимиты,
проверка Origin. Живую базу и соцсеть не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from core.api.sections import realtime as realtime_ws
from core.functions import presence, privacy as privacy_rules, realtime, realtime_social, sessions


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = None
        self.headers = {"user-agent": "Mozilla/5.0 Firefox/131"}

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000):
        self.closed = code

    def events(self, kind="presence"):
        return [m for m in self.sent if m.get("type") == kind]


def user(uid, name):
    return {"id": uid, "name": name, "avatar": f"https://social.test/a/{uid}.jpg", "profile_path": f"/@user{uid}"}


def broken_status():
    raise OSError("source registry is down")


class HubTests(unittest.TestCase):
    """Сакура (1) и Наруто (2) — друзья, Хината (3) Сакуре не друг."""

    def setUp(self):
        self.clock = FakeClock()
        self.store = presence.Presence(grace=30, clock=self.clock)
        self.privacy = {}
        self.titles = {52991: ("Фрирен, провожающая в последний путь", "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg")}
        self.hub = realtime.Hub(clock=self.clock)
        patches = [
            patch.object(presence, "presence", self.store),
            patch.object(privacy_rules, "for_users", side_effect=lambda ids: {u: self.privacy[u] for u in ids if u in self.privacy}),
            patch.object(presence, "title_cards", side_effect=lambda ids: {m: self.titles[m] for m in ids if m in self.titles}),
            # Hub без core.py: состояние сервера связываем явно, как core.py
            patch.object(realtime_social, "status_provider", lambda: {"ok": True, "sources": [], "vpn": False}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_async(self, coro):
        return asyncio.run(coro)

    def connect(self, uid, name, friends=()):
        ws = FakeWS()
        conn = self.hub.join(ws, user(uid, name) if uid else None, f"s{uid}" if uid else "", "198.51.100.7")
        if uid:
            self.hub.set_friends(uid, frozenset(friends))
        return conn, ws

    def frame(self, conn, **data):
        self.run_async(self.hub.handle(conn, json.dumps(data)))

    def test_a_friend_sees_at_once_who_came_and_what_they_watch(self):
        naruto, naruto_ws = self.connect(2, "Наруто", friends={1})
        sakura, sakura_ws = self.connect(1, "Сакура", friends={2})
        self.privacy[1] = privacy_rules.Privacy(show_watching=True)
        self.frame(sakura, type="visible", value=True)
        self.frame(sakura, type="watching", mal_id=52991, title="из браузера")
        came, watches = naruto_ws.events()
        self.assertEqual((came["user"]["id"], came["on_asoplay"], came["watching"]), (1, True, None))
        self.assertEqual(came["user"], {"id": 1, "name": "Сакура", "avatar": "https://social.test/a/1.jpg", "profile_path": "/@user1"})
        self.assertEqual(watches["watching"], {"mal_id": 52991, "title": "Фрирен, провожающая в последний путь",
                                               "poster": "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg", "path": "/anime/52991"})
        self.assertEqual(sakura_ws.events(), [])                        # Наруто ещё не на экране

    def test_nothing_is_sent_when_nothing_changed_for_friends(self):
        naruto, naruto_ws = self.connect(2, "Наруто", friends={1})
        sakura, _ = self.connect(1, "Сакура", friends={2})
        self.frame(sakura, type="visible", value=True)
        self.frame(sakura, type="watching", mal_id=52991, title="Фрирен")   # «что смотрю» не разрешено
        self.frame(sakura, type="visible", value=True)
        self.assertEqual(len(naruto_ws.events()), 1)

    def test_hidden_presence_reaches_nobody(self):
        _, naruto_ws = self.connect(2, "Наруто", friends={1})
        sakura, _ = self.connect(1, "Сакура", friends={2})
        self.privacy[1] = privacy_rules.Privacy(hide_presence=True, show_watching=True)
        self.frame(sakura, type="visible", value=True)
        self.frame(sakura, type="watching", mal_id=52991)
        self.assertEqual(naruto_ws.events(), [])

    def test_only_friends_hear_and_guests_cannot_mark_anyone(self):
        _, naruto_ws = self.connect(2, "Наруто", friends={1})
        hinata, _ = self.connect(3, "Хината", friends={4})
        guest, guest_ws = self.connect(0, "")
        self.frame(hinata, type="visible", value=True)
        self.frame(guest, type="visible", value=True)
        self.assertEqual(naruto_ws.events(), [])
        self.assertEqual(guest_ws.events(), [])
        self.assertNotIn(0, self.store.current())
        self.frame(guest, type="ping")
        self.assertEqual(guest_ws.events("pong"), [{"type": "pong"}])

    def test_leaving_is_announced_after_the_grace(self):
        _, naruto_ws = self.connect(2, "Наруто", friends={1})
        sakura, _ = self.connect(1, "Сакура", friends={2})
        self.frame(sakura, type="visible", value=True)
        self.run_async(self.hub.leave(sakura))
        self.assertEqual(len(naruto_ws.events()), 1)                    # перезагрузка страницы — не «вышел»
        self.clock.t += 31
        self.run_async(self.hub.sweep())
        left = naruto_ws.events()[-1]
        self.assertEqual((left["user"]["id"], left["on_asoplay"], left["watching"]), (1, False, None))
        self.assertNotIn(1, self.hub.briefs)

    def test_changed_privacy_is_announced_at_once(self):
        _, naruto_ws = self.connect(2, "Наруто", friends={1})
        sakura, _ = self.connect(1, "Сакура", friends={2})
        self.frame(sakura, type="visible", value=True)
        self.privacy[1] = privacy_rules.Privacy(hide_presence=True)
        self.run_async(self.hub.privacy_changed(1))
        self.assertEqual([e["on_asoplay"] for e in naruto_ws.events()], [True, False])

    def test_a_viewer_who_left_no_longer_receives_and_strangers_are_not_indexed(self):
        naruto, naruto_ws = self.connect(2, "Наруто", friends={1})
        sakura, _ = self.connect(1, "Сакура", friends={2})
        self.run_async(self.hub.leave(naruto))
        self.assertNotIn(1, self.hub.followers)
        self.hub.set_friends(7, frozenset({1}))                          # 7 не на сайте — индекс не растёт
        self.assertNotIn(1, self.hub.followers)
        self.frame(sakura, type="visible", value=True)
        self.assertEqual(naruto_ws.events(), [])

    def test_bad_frames_are_ignored(self):
        sakura, _ = self.connect(1, "Сакура")
        for text in ("not json", "[1, 2]", json.dumps({"type": "watching", "mal_id": True}),
                     json.dumps({"type": "visible", "value": True, "pad": "x" * realtime.MAX_FRAME})):
            self.run_async(self.hub.handle(sakura, text))
        self.assertEqual(self.store.current(), {})
        self.frame(sakura, type="visible", value=True)
        self.frame(sakura, type="watching", mal_id=-5, title="x")
        self.assertEqual(self.store.current()[1].mal_id, 0)

    def test_too_many_frames_are_dropped(self):
        sakura, ws = self.connect(1, "Сакура")
        for _ in range(realtime.FRAME_LIMIT + 5):
            self.frame(sakura, type="ping")
        self.assertEqual(len(ws.events("pong")), realtime.FRAME_LIMIT)
        self.clock.t += realtime.FRAME_WINDOW
        self.frame(sakura, type="ping")
        self.assertEqual(len(ws.events("pong")), realtime.FRAME_LIMIT + 1)

    def test_status_changes_reach_every_tab(self):
        _, guest_ws = self.connect(0, "")
        _, sakura_ws = self.connect(1, "Сакура")
        status = {"ok": True, "sources": ["kodik"], "vpn": False}
        with patch.object(realtime_social, "status_provider", lambda: dict(status)):
            self.run_async(self.hub.sweep())
            self.run_async(self.hub.sweep())
            self.assertEqual(guest_ws.events("status"), [])
            status["vpn"] = True
            self.run_async(self.hub.sweep())
        for ws in (guest_ws, sakura_ws):
            self.assertEqual(ws.events("status"), [{"type": "status", "status": {"ok": True, "sources": ["kodik"], "vpn": True}}])

    def test_status_without_binding_fails_clearly(self):
        with patch.object(realtime_social, "status_provider", None):
            with self.assertRaisesRegex(RuntimeError, "realtime_social.status_provider"):
                self.hub.status()

    def test_a_failing_status_keeps_the_last_known_state(self):
        known = {"ok": True, "sources": ["kodik"], "vpn": True}
        _, ws = self.connect(1, "Сакура")
        with patch.object(realtime_social, "status_provider", lambda: dict(known)):
            self.run_async(self.hub.sweep())
        with patch.object(realtime_social, "status_provider", broken_status), self.assertLogs("asoplay.realtime"):
            self.assertEqual(self.hub.status(), known)
            self.run_async(self.hub.sweep())
        # Ни «всё хорошо, источников нет», ни «всё плохо»: вкладкам ничего не пришло
        self.assertEqual(ws.events("status"), [])

    def test_a_status_failing_from_the_start_says_not_ok(self):
        with patch.object(realtime_social, "status_provider", broken_status), self.assertLogs("asoplay.realtime"):
            self.assertEqual(self.hub.status(), {"ok": False, "sources": [], "vpn": False})

    def test_records_moved_by_the_30_day_rule_reach_only_their_persons_tabs(self):
        _, tab1 = self.connect(1, "Сакура")
        _, tab2 = self.connect(1, "Сакура")
        _, naruto_ws = self.connect(2, "Наруто", friends={1})
        _, guest_ws = self.connect(0, "")
        items = [{"mal_id": 52991, "status": "dropped", "status_source": "auto"}]
        self.run_async(self.hub.lists_changed(1, items))
        for ws in (tab1, tab2):
            self.assertEqual(ws.events("lists"), [{"type": "lists", "items": items}])
        self.assertEqual((naruto_ws.events("lists"), guest_ws.events("lists")), ([], []))

    def test_logout_and_revoked_sessions_close_their_tabs(self):
        sakura, sakura_ws = self.connect(1, "Сакура")
        naruto, naruto_ws = self.connect(2, "Наруто")
        self.run_async(self.hub.drop_session("s1"))
        self.assertEqual((sakura_ws.closed, naruto_ws.closed), (4001, None))
        with patch.object(realtime, "session_user", side_effect=lambda s: None):
            self.run_async(self.hub.check_sessions())
        self.assertEqual(naruto_ws.closed, 4001)

    def test_limits_per_person_and_per_guest_address(self):
        with patch.object(realtime, "MAX_TABS_PER_USER", 2), patch.object(realtime, "MAX_GUESTS_PER_IP", 1):
            self.connect(1, "Сакура")
            self.assertTrue(self.hub.has_room(user(1, "Сакура"), "198.51.100.7"))
            self.connect(1, "Сакура")
            self.assertFalse(self.hub.has_room(user(1, "Сакура"), "198.51.100.7"))
            guest, _ = self.connect(0, "")
            self.assertFalse(self.hub.has_room(None, "198.51.100.7"))
            self.assertTrue(self.hub.has_room(None, "198.51.100.8"))
            self.run_async(self.hub.leave(guest))
            self.assertTrue(self.hub.has_room(None, "198.51.100.7"))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(realtime_ws.router)
        self.client = TestClient(app)
        self.store = presence.Presence(clock=FakeClock())
        self.hub = realtime.Hub()
        self.users = {"t" * 64: user(1, "Сакура")}
        patches = [
            patch.object(realtime, "hub", self.hub),
            patch.object(presence, "presence", self.store),
            patch.object(privacy_rules, "for_users", return_value={}),
            patch.object(realtime, "session_user", side_effect=lambda s: self.users.get(s)),
            patch.object(realtime_social, "status_provider", lambda: {"ok": True, "sources": ["kodik"], "vpn": False}),
            # Друзей здесь не грузим: фоновую задачу закрываем, не запуская
            patch.object(realtime.Hub, "spawn", lambda self, coro: coro.close()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_only_pages_of_this_site_may_connect(self):
        for headers in ({}, {"origin": "https://evil.test"}, {"origin": "null"}):
            with self.assertRaises(WebSocketDisconnect), self.client.websocket_connect("/ws", headers=headers) as ws:
                ws.receive_json()
        with patch.object(realtime_ws, "extra_origins", {"http://127.0.0.1:5500"}), \
                self.client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:5500"}) as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")

    def test_a_guest_gets_the_server_status(self):
        with self.client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
            self.assertEqual(ws.receive_json(), {"type": "hello", "user_id": None,
                                                 "status": {"ok": True, "sources": ["kodik"], "vpn": False}})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    def test_a_signed_in_tab_marks_its_person_and_leaving_starts_the_grace(self):
        self.client.cookies.set(sessions.COOKIE_NAME, "t" * 64)
        with self.client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
            self.assertEqual(ws.receive_json()["user_id"], 1)
            ws.send_json({"type": "visible", "value": True})
            ws.send_json({"type": "watching", "mal_id": 52991, "title": "Фрирен"})
            ws.send_json({"type": "ping"})
            ws.receive_json()                                            # pong: кадры выше уже обработаны
            self.assertEqual(self.store.current()[1].mal_id, 52991)
        self.assertEqual(self.hub.conns, {})
        self.assertIn(1, self.store.current())                           # ещё GRACE секунд после закрытия

    def test_a_full_house_is_refused(self):
        self.client.cookies.set(sessions.COOKIE_NAME, "t" * 64)
        with patch.object(realtime, "MAX_TABS_PER_USER", 1), \
                self.client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as first:
            first.receive_json()
            with self.assertRaises(WebSocketDisconnect), \
                    self.client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as second:
                second.receive_json()


if __name__ == "__main__":
    unittest.main()
