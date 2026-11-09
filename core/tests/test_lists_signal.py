"""
Сигнал соцсети lists_changed (clients.md, раздел 17): изменения одного человека за 2 секунды — одним кадром, и кадр
уходит на каждый путь изменения блока — статус руками и сам (upsert_status: «Смотрю», «Просмотрено», досмотр задним
числом), удаление, правило 30 дней, «Не смотрел», «Скрыть мои списки». Без базы и без соцсети.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import unittest
from unittest.mock import patch

from core.api.sections.account import settings as settings_section
from core.functions import lists, lists_signal, realtime, watch_history
from core.tests.as_events.lib import AT, FakeConn


class CoalesceTests(unittest.TestCase):
    def setUp(self):
        lists_signal._pending.clear()
        self.scheduled, self.sent = [], []
        for item in (patch.object(realtime.hub, "later_from_thread",
                                  side_effect=lambda delay, cb, *args: self.scheduled.append((delay, cb, args)) or True),
                     patch.object(realtime.hub.social, "send", side_effect=lambda frame: self.sent.append(frame) or True)):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(lists_signal._pending.clear)

    def test_changes_within_the_window_go_as_one_frame_per_person(self):
        for _ in range(3):
            lists_signal.changed(20)
        lists_signal.changed(21)
        self.assertEqual([(delay, args) for delay, _, args in self.scheduled], [(2.0, (20,)), (2.0, (21,))])
        for _, callback, args in self.scheduled:
            callback(*args)
        self.assertEqual(self.sent, [{"type": "lists_changed", "user_id": 20}, {"type": "lists_changed", "user_id": 21}])
        lists_signal.changed(20)  # окно кончилось — следующее изменение ждёт новый кадр
        self.assertEqual(len(self.scheduled), 3)

    def test_without_a_running_server_nothing_waits(self):
        with patch.object(realtime.hub, "later_from_thread", return_value=False):
            lists_signal.changed(22)
        self.assertNotIn(22, lists_signal._pending)


class ChangePathTests(unittest.TestCase):
    """Каждый путь изменения приходит в одну точку — lists.changed."""

    def setUp(self):
        self.signals = []
        item = patch.object(lists.lists_signal, "changed", side_effect=self.signals.append)
        item.start()
        self.addCleanup(item.stop)

    def test_a_status_set_by_hand_or_by_the_player_and_a_removal_signal(self):
        lists.upsert_status(FakeConn(), 20, 52991, "watching", "auto")
        lists.upsert_status(FakeConn(), 20, 52991, None, "manual")
        lists.remove_entry(FakeConn(), 21, 52991)
        self.assertEqual(self.signals, [20, 20, 21])

    def test_a_finished_title_signals_through_the_same_point(self):
        with patch.object(lists, "auto_add_enabled", return_value=True), \
                patch.object(lists, "fetch_entry", return_value=None), patch.object(lists, "record_event"):
            lists.auto_complete(FakeConn(), 20, 52991, at=AT)
        self.assertEqual(self.signals, [20])

    def test_the_30_day_rule_signals_each_moved_person_once(self):
        rows = [(20, 1, 0, "A", "", AT), (20, 2, 0, "B", "", AT), (21, 3, 0, "C", "", AT)]
        selects = iter([rows, []])

        def reply(sql, params):
            return (next(selects, []), 1) if sql.startswith("SELECT") else ([], 1)

        lists.sweep_dropped_after_30_days(FakeConn(reply), AT, batch=10)
        self.assertEqual(sorted(self.signals), [20, 21])

    def test_unwatch_signals_when_it_moves_an_auto_status(self):
        with patch.object(watch_history, "_list_entry_for_title", return_value={"status_source": "auto"}):
            watch_history.adjust_auto_list_status_after_unwatch(FakeConn(), 20, 52991, removed_episode=1, episodes_total=12)
        self.assertEqual(self.signals, [20])

    def test_hiding_lists_signals_but_other_privacy_flags_do_not(self):
        with patch.object(settings_section, "connect", return_value=FakeConn()), \
                patch.object(settings_section, "read_privacy", return_value={}), \
                patch.object(realtime, "privacy_changed"):
            settings_section.privacy_put(settings_section.PrivacyIn(hide_lists=True), {"id": 20})
            settings_section.privacy_put(settings_section.PrivacyIn(hide_presence=True), {"id": 21})
        self.assertEqual(self.signals, [20])


if __name__ == "__main__":
    unittest.main()
