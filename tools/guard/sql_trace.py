"""
Сторож рефакторинга: обработчики записи шлют в базу те же запросы, что до шага.

Автор: Чепела Даниил Максимович.

Сверка ответов (responses.py) видит только чтение. Здесь — запись: набор запросов на изменение гоняется через
TestClient с подменённым соединением (курсор записывает SQL и параметры и отвечает по сценарию), фиксированным
временем и перехваченными записью в дневник и сигналом realtime. База не трогается. Сравниваются ответы и
последовательность запросов — старой копии кода (git worktree на коммите до шага) и рабочей.

    python -B tools/guard/sql_trace.py <копия кода до шага> [сценарий]   выход 1 — если хоть что-то разошлось

Сценарии: account (по умолчанию), lists.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHILD = r'''
import json, logging, os, sys
from datetime import datetime
# Страховка: настоящая база недоступна — случайное соединение упадёт, а не запишет (порт 1 ни на одном хосте)
os.environ["AV_DB_PORT"] = "1"
sys.path.insert(0, ROOT_DIR)
logging.disable(logging.CRITICAL)
from fastapi import FastAPI
from fastapi.testclient import TestClient
import importlib
from core.classes import db
from core.functions import activity

def _first_attr(candidates, attr):
    """Объект из первого модуля, где он есть: в коде до шага и после он может лежать в разных местах."""
    for name in candidates:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        if hasattr(module, attr):
            return getattr(module, attr)
    raise SystemExit(f"нет {attr}: {candidates}")

def _router_module(candidates):
    """Модуль с роутером раздела: в коде до шага и после он может лежать в разных местах."""
    for name in candidates:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        if hasattr(module, "router"):
            return module
    raise SystemExit(f"нет роутера: {candidates}")

section = _router_module(ROUTER_MODULES)

FIXED = datetime(2026, 10, 30, 12, 0, 0)
ANSWERS = ANSWER_SET
trace = []

class Cursor:
    def __init__(self):
        self.rows, self.rowcount, self.lastrowid = [], 1, 0
    def execute(self, sql, params=()):
        flat = " ".join(str(sql).split())
        trace.append(["sql", flat, [p.isoformat() if hasattr(p, "isoformat") else p for p in params or ()]])
        # Как настоящая база: COUNT(*) всегда отвечает одной строкой
        default = [(0,)] if flat.upper().startswith("SELECT COUNT(") else []
        self.rows = next((rows for key, rows in ANSWERS if key in flat), default)
    def fetchall(self):
        return [tuple(r) for r in self.rows]
    def fetchone(self):
        return tuple(self.rows[0]) if self.rows else None

class Conn:
    def __init__(self):
        self.c = Cursor()
    def cursor(self):
        return self.c
    def commit(self):
        trace.append(["commit"])
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False

def fake_connect():
    return Conn()

def fake_record(conn, user_id, **kw):
    trace.append(["record_event", user_id, sorted(kw.items())])

# Оригиналы — до подмены: сравнивать с db.connect, который сам уже подменён, нельзя
REAL_CONNECT, REAL_RECORD = db.connect, activity.record_event
# Проверка тайтла в каталоге (ходит в MAL) — подменяется на «есть»: сети в проверке нет
_lists = sys.modules.get("core.functions.lists")
REAL_MAL = getattr(_lists, "mal_id_exists", None) or getattr(_lists, "_mal_id_exists", None)

async def fake_mal(mal_id):
    trace.append(["mal_id_exists", mal_id])
    return True
# Время: у кода до шага 5c — свои _now, после — общая core.classes.clock.utc_now
_clock = sys.modules.get("core.classes.clock")
REAL_UTC_NOW = getattr(_clock, "utc_now", None)
for name, module in list(sys.modules.items()):
    if not name.startswith("core.") or module is None:
        continue
    if getattr(module, "connect", None) is REAL_CONNECT:
        module.connect = fake_connect
    now = getattr(module, "_now", None)
    if callable(now) and getattr(now, "__module__", "") in ("core.api.sections.account", "core.functions.progress"):
        module._now = lambda: FIXED
    if getattr(module, "record_event", None) is REAL_RECORD:
        module.record_event = fake_record
    if REAL_UTC_NOW is not None and getattr(module, "utc_now", None) is REAL_UTC_NOW:
        module.utc_now = lambda: FIXED
    for attr in ("mal_id_exists", "_mal_id_exists"):
        if REAL_MAL is not None and getattr(module, attr, None) is REAL_MAL:
            setattr(module, attr, fake_mal)
left = [n for n, m in sys.modules.items() if n.startswith("core.") and m is not None
        and (getattr(m, "connect", None) is REAL_CONNECT or getattr(m, "record_event", None) is REAL_RECORD)]
if left:
    raise SystemExit(f"не подменено: {left}")
from core.functions import realtime
realtime.privacy_changed = lambda user_id: trace.append(["privacy_changed", user_id])

app = FastAPI()
app.include_router(section.router)
current_user_required = _first_attr(("core.functions.sessions", "core.classes.db"), "current_user_required")
app.dependency_overrides[current_user_required] = lambda: {"id": 999999999}  # такого человека нет
client = TestClient(app, raise_server_exceptions=False)  # ошибка сервера — тоже ответ, сравнивается как есть
out = []
for method, path, body in REQUESTS:
    trace.clear()
    resp = client.request(method, path, json=body)
    out.append({"request": f"{method} {path}", "status": resp.status_code, "body": resp.text, "trace": list(trace)})
print("@@" + json.dumps(out, ensure_ascii=False, default=str))
'''

ACCOUNT = [
    ("PUT", "/account/favorites/52991", {"mal_id": 52991, "title": "Фрирен", "poster_url": "https://p.example/1.jpg"}),
    ("PUT", "/account/favorites/52991", {"mal_id": 1, "title": "", "poster_url": ""}),
    ("DELETE", "/account/favorites/52991", None),
    ("PUT", "/account/history/52991", {"mal_id": 52991, "title": "Фрирен", "last_episode": 3, "episode_seconds": 700,
                                       "episode_duration": 1440, "episodes_total": 28}),
    ("DELETE", "/account/history/52991", None),
    ("DELETE", "/account/history", None),
    ("PUT", "/account/progress/52991", {"episode_num": 4, "seconds": 1400, "duration": 1440, "title": "Фрирен",
                                        "episodes_total": 28}),
    ("PUT", "/account/progress/52991", {"episode_num": 1, "seconds": 10}),
    ("DELETE", "/account/progress/52991/4", None),
    ("DELETE", "/account/progress/52991/99999", None),
    ("PUT", "/account/ratings/52991", {"score": 9}),
    ("DELETE", "/account/ratings/52991", None),
    ("PUT", "/account/dub-prefs/52991", {"dub_norm": "anilibria"}),
    ("PUT", "/account/settings", {"autonext": False}),
    ("PUT", "/account/settings", {"auto_add_lists": True, "extra": {"theme": "dark"}}),
    ("PUT", "/account/privacy", {"show_watching": True}),
    ("PUT", "/account/privacy", {"hide_lists": True, "hide_activity": False}),
    ("POST", "/account/import-localstorage", {
        "favorites": [{"mal_id": 1535, "title": "Тетрадь смерти", "cover": "c.jpg"}, {"id": "bad"}],
        "watch": {"52991": {"ep": 2, "time": 300, "duration": 1440, "title": "Фрирен"}, "x": {}},
        "ratings": {"1535": 10, "2": 11}, "dub_prefs": {"1535": "anilibria"}, "autonext": False}),
    ("POST", "/account/import-localstorage?force=1", {"favorites": [], "watch": {}, "ratings": {}, "dub_prefs": {}}),
]
# Ответы базы для второго прогона: строки находятся, ветки «уже есть» и «досмотрено» тоже проходят
ACCOUNT_FULL = [
    ("SELECT seconds, duration FROM aviev_episode_progress", [(1400, 1440)]),
    ("SELECT 1 FROM aviev_episode_progress", [(1,)]),
    ("FROM aviev_watch_history WHERE user_id=%s AND mal_id=%s LIMIT 1",
     [(52991, 4, 1400, 1440, 28, "Фрирен", "p.jpg", None)]),
    ("FROM aviev_user_lists WHERE user_id=%s AND mal_id=%s LIMIT 1",
     [(52991, "completed", "auto", 0, "Фрирен", "p.jpg", None, None)]),
    ("SELECT episode_num, seconds, duration, updated_at FROM aviev_episode_progress WHERE user_id=%s AND mal_id=%s ORDER BY updated_at",
     [(3, 1400, 1440, None)]),
    ("SELECT episode_num, seconds, duration FROM aviev_episode_progress", [(3, 1400, 1440), (2, 1440, 1440)]),
    ("SELECT kind FROM aviev_import_marks", [("favorites",), ("ratings",)]),
    ("FROM aviev_privacy WHERE user_id", [(0, 1, 0, 1)]),
]
LISTS = [
    ("GET", "/account/lists", None),
    ("GET", "/account/lists?status=favorite", None),
    ("GET", "/account/lists/counts", None),
    ("GET", "/account/lists/52991", None),
    ("PUT", "/account/lists/52991/status", {"status": "watching", "title": "Фрирен"}),
    ("PUT", "/account/lists/52991/status", {"status": None}),
    ("PUT", "/account/lists/52991/status", {"status": "bogus"}),
    ("PUT", "/account/lists/52991/favorite", {"is_favorite": True, "title": "Фрирен", "poster_url": "p.jpg"}),
    ("PUT", "/account/lists/52991/favorite", {"is_favorite": False}),
    ("DELETE", "/account/lists/52991", None),
    ("POST", "/account/lists/progress-event", {"mal_id": 52991, "episode_num": 3, "seconds": 1400, "duration": 1440,
                                               "episodes_total": 3, "title": "Фрирен"}),
    ("POST", "/account/lists/progress-event", {"mal_id": 52991, "episode_num": 1, "seconds": 5}),
    ("POST", "/account/lists/sweep", None),
]
LISTS_FULL = [
    ("FROM aviev_user_lists WHERE user_id=%s AND mal_id=%s", [(5, 52991, "watching", "auto", 0, "Фрирен", "p.jpg", None, None)]),
    ("SELECT auto_add_lists FROM aviev_account_settings", [(1,)]),
    # Ключ — с WHERE: иначе он совпал бы с подзапросом NOT EXISTS в выборке правила 30 дней
    ("SELECT 1 FROM aviev_episode_progress WHERE user_id=%s", [(1,)]),
    ("FROM aviev_user_lists l WHERE", [(999999999, 52991, 0, "Фрирен", "p.jpg", None)]),
    ("GROUP BY status", [("watching", 2), ("planned", 1)]),
    ("SELECT COUNT(*) FROM aviev_user_lists", [(3,)]),
    ("FROM aviev_user_lists WHERE user_id=%s AND", [(5, 52991, "watching", "auto", 1, "Фрирен", "p.jpg", None, None)]),
]
SCENARIOS = {
    "account": (ACCOUNT, [[], ACCOUNT_FULL], ["core.api.sections.account"]),
    "lists": (LISTS, [[], LISTS_FULL], ["core.api.sections.lists", "core.functions.lists"]),
}


def _run(root: Path, requests: list, answers: list, routers: list) -> list:
    """Прогнать запросы на копии кода root в отдельном процессе (модули старой и новой копии не смешиваются)."""
    code = CHILD.replace("ROOT_DIR", repr(str(root))).replace("ANSWER_SET", repr(answers)).replace(
        "REQUESTS", repr(requests)).replace("ROUTER_MODULES", repr(routers))
    proc = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("@@")), None)
    if line is None:
        raise SystemExit(f"{root}: прогон упал\n{proc.stderr[-3000:]}")
    return json.loads(line[2:])


def main(argv: list[str]) -> int:
    """Сравнить запись старой копии кода и рабочей."""
    if not argv:
        raise SystemExit(__doc__)
    old_root = Path(argv[0]).resolve()
    requests, answer_sets, routers = SCENARIOS[argv[1] if len(argv) > 1 else "account"]
    changed = 0
    total = 0
    for answers in answer_sets:
        old, new = _run(old_root, requests, answers, routers), _run(ROOT, requests, answers, routers)
        for a, b in zip(old, new):
            total += 1
            if a != b:
                changed += 1
                print(f"== ИЗМЕНИЛАСЬ ЗАПИСЬ: {a['request']} (ответы базы: {'есть' if answers else 'пусто'})")
                print("   было ", json.dumps(a, ensure_ascii=False)[:700])
                print("   стало", json.dumps(b, ensure_ascii=False)[:700])
    queries = sum(len(r["trace"]) for r in new)
    print(f"sql_trace: {total} requests, {changed} changed (last run: {queries} queries and events)")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
