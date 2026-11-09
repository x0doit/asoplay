"""
Комментарии через соцсеть (clients.md, раздел 12): только объекты AsoPlay, гостям — короткий кеш по нормализованной
строке запроса, вошедшим — их токен без кеша; истёк токен доступа — одно обновление и повтор; название и адрес ветки
ставит сервер; отказы соцсети — короткими кодами, текст — в журнал; события веток — только подписанным вкладкам.
Без сети и без базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.api.sections import comments as routes
from core.classes import as_api, as_sessions
from core.functions import comments, realtime, sessions

LIST = {"items": [], "next_cursor": None, "total": 0, "comments_count": 0}


class Calls:
    """Поддельная соцсеть: записывает вызовы, отвечает по очереди answers (исключение — бросает)."""

    def __init__(self, *answers):
        self.answers = list(answers) or [LIST]
        self.seen = []

    def __call__(self, method, path, *, token=None, body=None, user_ip="", user_agent=""):
        self.seen.append((method, path, token, body))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def client(token="tok", social=None):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[sessions.current_user_required] = lambda: {"id": 7}
    social = social or Calls()
    for p in (patch.object(comments, "token_of", return_value=token), patch.object(as_api, "call", social)):
        p.start()
    return TestClient(app), social


class Base(unittest.TestCase):
    def setUp(self):
        comments._guest.clear()
        self.addCleanup(patch.stopall)


class Objects(Base):
    def test_only_titles_and_episodes_of_asoplay(self):
        self.assertEqual(comments.parse_object("asoplay:anime:52991"), (52991, None))
        self.assertEqual(comments.parse_object("asoplay:episode:52991.6"), (52991, 6))
        for key in ("asoplay:anime:", "other:anime:1", "asoplay:collection:1", "asoplay:episode:1", "asoplay:anime:1x"):
            self.assertIsNone(comments.parse_object(key), key)
        http, social = client()
        self.assertEqual(http.get("/comments", params={"object": "other:anime:1"}).status_code, 422)
        self.assertEqual(social.seen, [])

    def test_the_query_is_normalised(self):
        self.assertEqual(comments.query({"sort": "new", "object": "o", "cursor": None, "limit": 20}),
                         comments.query({"limit": 20, "object": "o", "sort": "new"}))


class Reading(Base):
    def test_a_guest_reads_without_a_token_and_a_repeat_comes_from_the_cache(self):
        http, social = client(token=None)
        for params in ({"object": "asoplay:anime:1", "sort": "top", "limit": 20},
                       {"limit": 20, "sort": "top", "object": "asoplay:anime:1"}):  # тот же запрос, другой порядок
            self.assertEqual(http.get("/comments", params=params).json(), LIST)
        self.assertEqual(len(social.seen), 1)
        self.assertIsNone(social.seen[0][2])
        with patch.object(comments.time, "monotonic", return_value=comments.time.monotonic() + comments.GUEST_TTL + 1):
            http.get("/comments", params={"object": "asoplay:anime:1", "sort": "top"})
        self.assertEqual(len(social.seen), 2)

    def test_a_signed_in_person_reads_with_the_token_and_never_from_the_cache(self):
        http, social = client(token="tok")
        for _ in range(2):
            http.get("/comments", params={"object": "asoplay:anime:1"})
        self.assertEqual([s[2] for s in social.seen], ["tok", "tok"])
        self.assertEqual(comments._guest, {})

    def test_chat_windows_need_sort_moment_and_allow_100(self):
        http, social = client(token=None)
        ok = http.get("/comments", params={"object": "asoplay:episode:1.2", "sort": "moment", "from": 720, "to": 780,
                                           "limit": 100})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("from=720", social.seen[0][1])
        self.assertEqual(http.get("/comments", params={"object": "asoplay:anime:1", "from": 5}).status_code, 422)
        self.assertEqual(http.get("/comments", params={"object": "asoplay:anime:1", "limit": 100}).status_code, 422)


class Writing(Base):
    def lookup(self, slug="frieren", title_ru="Фрирен"):
        info = {"mal_id": 52991, "slug": slug, "title_ru": title_ru, "title_en": "Frieren"}
        patch.object(routes.title_cache, "lookup", AsyncMock(return_value=info)).start()
        url = lambda mal_id, s: f"https://asoplay.test/anime/{mal_id}-{s}/"  # noqa: E731
        patch.object(routes.title_cache, "canonical_url", url).start()

    def test_title_and_url_come_from_the_catalog_not_from_the_browser(self):
        self.lookup()
        http, social = client(social=Calls({"id": 1}))
        r = http.post("/comments", json={"object": "asoplay:anime:52991", "text": "  Лучшее аниме  ",
                                         "title": "подмена", "url": "https://evil.test/"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(social.seen[0][3], {"object": "asoplay:anime:52991", "text": "Лучшее аниме",
                                             "title": "Фрирен", "url": "https://asoplay.test/anime/52991-frieren/"})

    def test_an_episode_thread_names_the_episode_and_carries_the_moment(self):
        self.lookup()
        http, social = client(social=Calls({"id": 2}))
        http.post("/comments", json={"object": "asoplay:episode:52991.6", "text": "Вот это поворот", "moment": 754,
                                     "thread": 9, "reply_to": 10})
        body = social.seen[0][3]
        self.assertEqual((body["title"], body["url"]),
                         ("Фрирен, 6 серия", "https://asoplay.test/anime/52991-frieren/?ep=6"))
        self.assertEqual((body["moment"], body["thread"], body["reply_to"]), (754, 9, 10))

    def test_bad_input_and_missing_links(self):
        self.lookup()
        http, social = client()
        cases = [({"object": "asoplay:anime:1", "text": "x", "moment": 3}, 422),   # момент — только у серии
                 ({"object": "asoplay:anime:1", "text": "   "}, 422),
                 ({"object": "asoplay:anime:1", "text": "x" * 5001}, 422),
                 ({"object": "evil:anime:1", "text": "x"}, 422)]
        for body, status in cases:
            self.assertEqual(http.post("/comments", json=body).status_code, status, body)
        self.assertEqual(social.seen, [])
        http2, _ = client(token=None)
        r = http2.post("/comments", json={"object": "asoplay:anime:1", "text": "x"})
        self.assertEqual((r.status_code, r.json()["detail"]), (401, "as_link_required"))

    def test_report_reasons_come_from_the_list(self):
        http, social = client(social=Calls({"ok": True}))
        self.assertEqual(http.post("/comments/5/report", json={"reason": "boring"}).status_code, 422)
        report = http.post("/comments/5/report", json={"reason": "spam", "comment": " реклама "})
        self.assertEqual(report.status_code, 200)
        self.assertEqual(social.seen,
                         [("POST", "/clients/comments/5/report", "tok", {"reason": "spam", "comment": "реклама"})])


class Refusals(Base):
    def status(self, error, path="/comments/5/like"):
        http, _ = client(social=Calls(error))
        r = http.put(path)
        return r.status_code, r.json().get("detail"), r.headers.get("retry-after")

    def test_refusals_keep_the_code_and_lose_the_text(self):
        limited = as_api.ASApiError(429, "rate_limited", "x", retry_after=42)
        self.assertEqual(self.status(limited), (429, "rate_limited", "42"))
        self.assertEqual(self.status(as_api.ASApiError(403, "blacklisted", "x"))[:2], (403, "blacklisted"))
        self.assertEqual(self.status(as_api.ASApiError(409, "already_reported", "x"))[:2], (409, "already_reported"))
        with self.assertLogs("asoplay.comments", "WARNING") as logs:
            self.assertEqual(self.status(as_api.ASApiError(500, "server_error", "secret detail"))[:2],
                             (503, "comments_unavailable"))
            self.assertEqual(self.status(as_api.ASApiError(401, "invalid_client", "keys"))[:2],
                             (503, "comments_unavailable"))
        self.assertIn("secret detail", logs.output[0])

    def test_an_expired_access_token_is_refreshed_once_and_the_request_repeated(self):
        expired = as_api.ASApiError(401, "token_expired")
        http, social = client(social=Calls(expired, {"ok": True}))
        with patch.object(as_sessions, "access_token", return_value="fresh") as refresh:
            self.assertEqual(http.put("/comments/5/like", cookies={sessions.COOKIE_NAME: "s"}).status_code, 200)
        self.assertEqual([s[2] for s in social.seen], ["tok", "fresh"])
        self.assertEqual(refresh.call_args.kwargs, {"stale": "tok"})

    def test_a_second_401_means_no_link(self):
        expired = as_api.ASApiError(401, "token_expired")
        http, social = client(social=Calls(expired, expired))
        with patch.object(as_sessions, "access_token", return_value="fresh"), \
                patch.object(as_sessions, "forget_if_dead", return_value=False):
            r = http.put("/comments/5/like", cookies={sessions.COOKIE_NAME: "s"})
        self.assertEqual((r.status_code, r.json()["detail"], len(social.seen)), (401, "as_link_required", 2))

    def test_a_dead_link_still_lets_the_person_read_as_a_guest(self):
        http, social = client(social=Calls(as_api.ASApiError(401, "unauthorized"), LIST))
        with patch.object(as_sessions, "forget_if_dead", return_value=True):
            r = http.get("/comments", params={"object": "asoplay:anime:1"}, cookies={sessions.COOKIE_NAME: "s"})
        self.assertEqual((r.status_code, [s[2] for s in social.seen]), (200, ["tok", None]))


class StaleToken(unittest.TestCase):
    def test_a_stale_token_is_refreshed_only_if_nobody_refreshed_it_yet(self):
        row = {"access": "old", "refresh": "r1", "access_expires_at": 10**12}
        tokens = {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}
        with patch.object(as_sessions, "_load", side_effect=lambda s: dict(row)), \
                patch.object(as_sessions, "_update", side_effect=lambda s, t: row.update(access=t["access_token"])), \
                patch.object(as_api, "refresh", return_value=tokens) as refresh:
            self.assertEqual(as_sessions.access_token("s"), "old")              # по сроку живой — без обновления
            self.assertEqual(as_sessions.access_token("s", stale="old"), "new")  # соцсеть его не приняла
            self.assertEqual(as_sessions.access_token("s", stale="old"), "new")  # уже обновлён: второго нет
        refresh.assert_called_once()


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000):
        pass


class HubSubscriptions(unittest.TestCase):
    def setUp(self):
        self.hub = realtime.Hub()
        self.guest = self.hub.join(FakeWS(), None, "", "203.0.113.5")
        self.other = self.hub.join(FakeWS(), None, "", "203.0.113.6")

    def frame(self, conn, data):
        asyncio.run(self.hub.handle(conn, __import__("json").dumps(data)))

    def test_a_guest_subscribes_and_only_valid_keys_are_kept(self):
        self.frame(self.guest, {"type": "comments.watch",
                                "objects": ["asoplay:anime:1", "evil:x:1", "asoplay:episode:1.2", "asoplay:anime:3"]})
        self.assertEqual(self.guest.comment_objects, {"asoplay:anime:1"})  # из первых двух годен один
        self.frame(self.guest, {"type": "comments.watch", "objects": ["asoplay:anime:1", "asoplay:episode:1.2"]})
        self.assertEqual(self.hub.by_object,
                         {"asoplay:anime:1": {self.guest.id}, "asoplay:episode:1.2": {self.guest.id}})

    def test_events_go_only_to_subscribed_tabs_and_bad_events_are_dropped(self):
        self.frame(self.guest, {"type": "comments.watch", "objects": ["asoplay:episode:1.2"]})
        event = {"object": "asoplay:episode:1.2", "comment_id": 812, "thread": None, "moment": 754, "action": "added"}
        asyncio.run(self.hub.on_social_event("client_comment", event, []))
        asyncio.run(self.hub.on_social_event("client_comment", dict(event, action="hacked"), []))
        asyncio.run(self.hub.on_social_event("client_comment", dict(event, object="other:x:1"), []))
        asyncio.run(self.hub.on_social_event("client_comment", dict(event, action="liked", likes_count=3), []))
        self.assertEqual([m["action"] for m in self.guest.ws.sent], ["added", "liked"])
        self.assertEqual(self.guest.ws.sent[0], {"type": "comment", **event})
        self.assertEqual(self.guest.ws.sent[1]["likes_count"], 3)
        self.assertEqual(self.other.ws.sent, [])

    def test_leaving_cleans_the_index(self):
        self.frame(self.guest, {"type": "comments.watch", "objects": ["asoplay:anime:1"]})
        asyncio.run(self.hub.leave(self.guest))
        self.assertEqual(self.hub.by_object, {})


if __name__ == "__main__":
    unittest.main()
