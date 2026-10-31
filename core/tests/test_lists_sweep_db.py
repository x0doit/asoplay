"""
Правило 30 дней на настоящей базе: два процесса сразу — переносит ровно один, и не больше раза за назначенный срок.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Пишет в базу, поэтому по умолчанию пропускается: идёт только при AV_TESTS_DB=1 и только на базе этой машины (все
адреса хоста — loopback), иначе отказ. Данные — два временных аккаунта (core/tests/temp_account.py): проверка создаёт
их и удаляет сама. Время прохода — 2000 год, поэтому под условие попадают только их строки; до прохода это
проверяется. Задача и замок свои (lists_sweep_test), так что отметку настоящей задачи проверка не трогает.
"""
import ipaddress
import json
import os
import socket
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.classes.db import connect
from core.config import config, env
from core.functions import lists_sweep
from core.tests.temp_account import TempAccount

ROOT = Path(__file__).resolve().parents[2]
NAME = "lists_sweep_test"
NOW = datetime(2000, 2, 1, 4, 30)                                   # после 04:00 — проход этого дня
OLD, RECENT = datetime(2000, 1, 1, 12, 0), datetime(2000, 1, 25, 12, 0)
LISTS = ("INSERT INTO aviev_user_lists (user_id, mal_id, status, status_source, is_favorite, title, poster_url, "
         "added_at, updated_at) VALUES (%s, %s, %s, %s, 0, '', '', %s, %s)")
PROGRESS = ("INSERT INTO aviev_episode_progress (user_id, mal_id, episode_num, seconds, duration, updated_at) "
            "VALUES (%s, %s, 1, 600, 1440, %s)")
SETTINGS = ("INSERT INTO aviev_account_settings (user_id, autonext, auto_add_lists, settings_json, updated_at) "
            "VALUES (%s, 1, 0, NULL, %s)")
# Первый аккаунт: переносится только 101; остальные — ручной статус, свежий просмотр, свежая запись, не «Смотрю»
SEED_A = [(LISTS, [(101, "watching", "auto", OLD, OLD), (102, "watching", "manual", OLD, OLD),
                   (103, "watching", "auto", OLD, OLD), (104, "watching", "auto", RECENT, RECENT),
                   (105, "completed", "auto", OLD, OLD)]),
          (PROGRESS, [(103, RECENT)])]
# Второй выключил автодобавление — его записи правило не трогает
SEED_B = [(LISTS, [(201, "watching", "auto", OLD, OLD)]), (SETTINGS, [(OLD,)])]
CHILD = ("import json, logging, sys; root, now, name = sys.argv[1:]; del sys.argv[1:]; sys.path.insert(0, root); "
         "logging.disable(logging.CRITICAL); from datetime import datetime; from core.config import env; env.load(); "
         "from core.functions import lists_sweep; "
         "print('@@' + json.dumps(lists_sweep.run_once(datetime.fromisoformat(now), name=name)))")


def _local(host: str) -> bool:
    """База на этой машине: все адреса хоста — loopback (127.0.0.0/8, ::1)."""
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return bool(addrs) and all(ipaddress.ip_address(a).is_loopback for a in addrs)


def _query(sql: str, params: tuple) -> list[tuple]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return list(cur.fetchall())


class TwoProcessesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("AV_TESTS_DB") != "1":
            raise unittest.SkipTest("пишет в базу: только с AV_TESTS_DB=1 (core/tests/README.md)")
        env.load()
        host = config.db_settings()["host"]
        if not _local(host):
            raise RuntimeError(f"AV_TESTS_DB=1, но база не на этой машине ({host}) — отказ")
        cls.addClassCleanup(cls._drop_task)
        cls.a, cls.b = TempAccount(SEED_A, handle="asoplay-guard-a"), TempAccount(SEED_B, handle="asoplay-guard-b")
        cls.enterClassContext(cls.a)
        cls.enterClassContext(cls.b)
        others = _query("SELECT COUNT(*) FROM aviev_user_lists WHERE status = 'watching' AND status_source = 'auto' "
                        "AND updated_at < %s AND user_id NOT IN (%s, %s)",
                        (NOW - timedelta(days=30), cls.a.user_id, cls.b.user_id))[0][0]
        if others:
            raise RuntimeError(f"под условие прохода 2000 года попали чужие строки ({others}) — отказ")

    @classmethod
    def _drop_task(cls):
        with connect() as conn:
            conn.cursor().execute("DELETE FROM aviev_task_runs WHERE name = %s", (NAME,))
            conn.commit()

    def lists(self, user_id):
        rows = _query("SELECT mal_id, status, status_source, updated_at FROM aviev_user_lists WHERE user_id = %s",
                      (user_id,))
        return {mal_id: (status, source, at) for mal_id, status, source, at in rows}

    def test_two_processes_move_each_row_once_and_once_per_slot(self):
        procs = [subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(ROOT), NOW.isoformat(), NAME], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=dict(os.environ, PYTHONIOENCODING="utf-8")) for _ in range(2)]
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            line = next((ln for ln in out.splitlines() if ln.startswith("@@")), None)
            self.assertIsNotNone(line, err[-2000:])
            results.append(json.loads(line[2:]))
        self.assertEqual(sorted(results, key=lambda r: -1 if r is None else r), [None, 1])   # второй ничего не сделал
        self.assertEqual(self.lists(self.a.user_id), {
            101: ("dropped", "auto", NOW), 102: ("watching", "manual", OLD), 103: ("watching", "auto", OLD),
            104: ("watching", "auto", RECENT), 105: ("completed", "auto", OLD)})
        self.assertEqual(self.lists(self.b.user_id), {201: ("watching", "auto", OLD)})
        self.assertEqual(_query("SELECT COUNT(*) FROM aviev_activity WHERE user_id IN (%s, %s)",
                                (self.a.user_id, self.b.user_id))[0][0], 0)       # в график профиля — ничего
        started, finished, processed = _query("SELECT started_at, finished_at, processed FROM aviev_task_runs "
                                              "WHERE name = %s", (NAME,))[0]
        self.assertEqual((started, finished is not None, processed), (NOW, True, 1))
        self.assertIsNone(lists_sweep.run_once(NOW + timedelta(hours=1), name=NAME))      # тот же срок — не повторяется
        self.assertEqual(lists_sweep.run_once(NOW + timedelta(days=1), name=NAME), 0)     # следующий день — снова


if __name__ == "__main__":
    unittest.main()
