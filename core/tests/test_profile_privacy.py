"""
Открытый профиль: при «Скрыть мои списки» действия со списками не видны постороннему ни в ленте, ни в графике
активности; страница, заблокированная или удалённая в соцсети, — 404. Без базы и без соцсети: ответы подменены.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from core.api.sections import profile
from core.classes import as_api
from core.functions import activity_feed, social_pages
from core.functions.activity import LIST_KINDS
from core.modules import profile_pages
from core.tests.as_events.lib import FakeConn

TARGET = {"id": 7, "handle": "naruto", "profile_path": "/@naruto", "name": "Наруто", "avatar": "", "avatar_small": ""}
OPEN = {"hide_lists": False, "hide_activity": False, "hide_presence": False, "show_watching": False}
LISTS_HIDDEN = {**OPEN, "hide_lists": True}


class HiddenListsTests(unittest.TestCase):
    def without_seen(self, privacy, viewer):
        """Что профиль просит не показывать в графике и ленте: сводка, график и лента по очереди."""
        seen = []
        with patch.object(profile, "resolve_handle", return_value=TARGET), \
                patch.object(social_pages, "closed", return_value=False), \
                patch.object(profile, "read_privacy", return_value=privacy), \
                patch.object(profile.lists, "counts_for", return_value={}), \
                patch.object(profile, "graph_for_user_public", side_effect=lambda uid, days, without: seen.append(without)), \
                patch.object(profile, "fetch_recent_paged", side_effect=lambda uid, **kw: seen.append(kw["without"])):
            profile.profile_summary("naruto", days=30, viewer=viewer)
            profile.profile_activity("naruto", days=30, viewer=viewer)
            profile.profile_activity_recent("naruto", offset=0, limit=10, group="all", date=None, viewer=viewer)
        return seen

    def test_a_stranger_sees_no_list_actions_when_lists_are_hidden(self):
        self.assertEqual(self.without_seen(LISTS_HIDDEN, None), [LIST_KINDS] * 3)
        self.assertEqual(self.without_seen(LISTS_HIDDEN, {"id": 8}), [LIST_KINDS] * 3)

    def test_the_owner_and_open_lists_hide_nothing(self):
        self.assertEqual(self.without_seen(LISTS_HIDDEN, {"id": 7}), [frozenset()] * 3)
        self.assertEqual(self.without_seen(OPEN, None), [frozenset()] * 3)

    def test_list_kinds_are_list_and_favourite_actions_but_not_watching_or_ratings(self):
        self.assertEqual(LIST_KINDS, {"list_add", "list_move", "list_remove", "favorite", "unfavorite", "complete"})

    def test_the_graph_and_the_feed_leave_hidden_kinds_out_of_every_query(self):
        conn = FakeConn(lambda sql, params: ([], 0))
        with patch.object(activity_feed, "connect", return_value=conn):
            activity_feed.graph_for_user(7, days=30, without=LIST_KINDS)
            activity_feed.fetch_recent_paged(7, offset=0, limit=10, group=None, without=LIST_KINDS)
        queries = [(sql, params) for sql, params in conn.sql if "FROM aviev_activity" in sql]
        self.assertEqual(len(queries), 4)  # день, вид, лента в графике, лента страницей
        for sql, params in queries:
            self.assertIn(f"a.kind NOT IN ({','.join(['%s'] * 6)})", sql)
            self.assertTrue(set(LIST_KINDS) <= set(params))

        plain = FakeConn(lambda sql, params: ([], 0))
        with patch.object(activity_feed, "connect", return_value=plain):
            activity_feed.graph_for_user(7, days=30)
        self.assertFalse(any("a.kind NOT IN (%s" in sql for sql, _ in plain.sql))


class ClosedPageTests(unittest.TestCase):
    def setUp(self):
        social_pages._cache.clear()

    def tearDown(self):
        social_pages._cache.clear()

    def answer(self, state):
        return patch.object(as_api, "call", return_value={"items": [{"id": 7, "deactivated": state}]})

    def test_a_banned_or_deleted_page_has_no_open_profile(self):
        for state in ("banned", "deleted"):
            social_pages._cache.clear()
            with self.answer(state), patch.object(profile, "resolve_handle", return_value=TARGET), \
                    patch.object(profile_pages, "resolve_handle", return_value=TARGET):
                with self.assertRaises(HTTPException) as caught:
                    profile.profile_lists("naruto", status="watching", viewer=None)
                self.assertEqual(caught.exception.status_code, 404)
                self.assertEqual(profile_pages.profile_page("naruto").status_code, 404)

    def test_an_open_page_is_asked_once_while_the_answer_is_fresh(self):
        with self.answer(None) as call:
            self.assertFalse(social_pages.closed(7))
            self.assertFalse(social_pages.closed(7))
        call.assert_called_once_with("GET", "/users?ids=7")

    def test_without_an_answer_the_last_one_counts_and_with_none_the_page_stays_open(self):
        with self.answer("banned"), patch.object(social_pages.time, "monotonic", return_value=1000.0):
            self.assertTrue(social_pages.closed(7))
        down = patch.object(as_api, "call", side_effect=as_api.ASApiError(503, "unavailable"))
        later = patch.object(social_pages.time, "monotonic", return_value=1000.0 + social_pages.CACHE_SECONDS + 1)
        with down, later:
            self.assertTrue(social_pages.closed(7))
            self.assertFalse(social_pages.closed(8))

    def test_memory_has_a_limit_dropping_expired_answers_first_then_the_oldest(self):
        def ask(user_id, at):
            with patch.object(social_pages.time, "monotonic", return_value=at):
                social_pages.closed(user_id)

        with self.answer(None), patch.object(social_pages, "CACHE_MAX", 3):
            for uid in (1, 2, 3):
                ask(uid, 0.0)
            ask(4, 10.0)  # никто не просрочен — уходит самый давний
            self.assertEqual(list(social_pages._cache), [2, 3, 4])
            ask(5, social_pages.CACHE_SECONDS + 5.0)  # 2 и 3 просрочены — уходят они, свежий 4 остаётся
            self.assertEqual(list(social_pages._cache), [4, 5])

    def test_forget_makes_the_next_question_go_to_the_social_network(self):
        with self.answer(None) as call:
            social_pages.closed(7)
            social_pages.forget(7)
            social_pages.closed(7)
        self.assertEqual(call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
