"""
Сторож: SQL фоновых проходов меняется только с ведома — снимок через --save, дальше сверка с ним.

Автор: Чепела Даниил Максимович.

sql_trace.py сравнивает запись обработчиков со старой копией кода. Фоновым проходам сравнивать не с чем (старый
проход намеренно другой), поэтому здесь — снимок. Проход гоняется на подменённом соединении: курсор записывает SQL
и параметры и отвечает по сценарию, время фиксировано. Процесс отдельный, со страховкой AV_DB_PORT=1, база
не трогается. Снимок получен из кода, а не с машины, поэтому лежит в git.

    python -B tools/guard/sql_snapshot.py --save   записать снимок (baseline/sql-snapshot.json)
    python -B tools/guard/sql_snapshot.py          сравнить; выход 1 — если SQL прохода изменился

Сценарии правила 30 дней (core/functions/lists_sweep.py):
    due     срок пришёл: замок, отметка начала, две пачки по курсору (одну строку UPDATE не взял), кадры, конец;
    busy    замок у другого процесса;
    done    после назначенного времени проход уже был;
    person  ручной прогон одного человека (POST /account/lists/sweep).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).with_name("baseline") / "sql-snapshot.json"

CHILD = r'''
import json, logging, os, sys
from datetime import datetime
# Страховка: настоящая база недоступна — случайное соединение упадёт, а не запишет (порт 1 ни на одном хосте)
os.environ["AV_DB_PORT"] = "1"
sys.path.insert(0, ROOT_DIR)
logging.disable(logging.CRITICAL)
from core.classes import db
from core.functions import lists, lists_sweep

NOW, FINISHED = datetime(2026, 10, 31, 4, 30, 15), datetime(2026, 10, 31, 4, 31)
ADDED = datetime(2026, 9, 1, 12, 0)
trace = []


class Cursor:
    def __init__(self, answers, rowcounts):
        self.answers = {key: list(rows) for key, rows in answers.items()}
        self.rowcounts, self.rows, self.rowcount = list(rowcounts), [], 0

    def execute(self, sql, params=()):
        flat = " ".join(str(sql).split())
        trace.append(["sql", flat, [p.isoformat() if hasattr(p, "isoformat") else p for p in params or ()]])
        key = next((k for k in self.answers if k in flat), None)
        self.rows = self.answers[key].pop(0) if key and self.answers[key] else []
        if flat.startswith("UPDATE aviev_user_lists"):
            self.rowcount = self.rowcounts.pop(0) if self.rowcounts else 0

    def fetchall(self):
        return [tuple(r) for r in self.rows]

    def fetchone(self):
        return tuple(self.rows[0]) if self.rows else None


class Conn:
    def __init__(self, cursor):
        self.c = cursor

    def cursor(self):
        return self.c

    def commit(self):
        trace.append(["commit"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def frames(entries):
    trace.append(["frames", [[user_id, e["mal_id"], e["status"], e["updated_at"]] for user_id, e in entries]])


rows = lambda *keys: [(u, m, 0, f"Тайтл {m}", "", ADDED) for u, m in keys]
SWEEP = {"FROM aviev_user_lists l": [rows((5, 1), (5, 2)), rows((7, 3))]}
CASES = [
    ("due", {"GET_LOCK": [[(1,)]], "FROM aviev_task_runs": [[(datetime(2026, 10, 30, 4, 0), datetime(2026, 10, 30, 4, 1))]],
             **SWEEP}, [1, 0, 1]),
    ("busy", {"GET_LOCK": [[(0,)]]}, []),
    ("done", {"GET_LOCK": [[(1,)]], "FROM aviev_task_runs": [[(datetime(2026, 10, 31, 4, 0, 5), FINISHED)]]}, []),
]
REAL_CONNECT = db.connect
lists_sweep.utc_now = lambda: FINISHED
out = []
for case, answers, rowcounts in CASES:
    trace.clear()
    conn = Conn(Cursor(answers, rowcounts))
    lists_sweep.connect = lambda conn=conn: conn
    result = lists_sweep.run_once(NOW, batch=2, on_batch=frames)
    out.append({"case": case, "result": result, "trace": list(trace)})
trace.clear()
person = Conn(Cursor({"FROM aviev_user_lists l": [rows((5, 1), (5, 2)), []]}, [1, 1]))
result = lists.sweep_dropped_after_30_days(person, NOW, user_id=5, batch=2, on_batch=frames)
out.append({"case": "person", "result": result, "trace": list(trace)})
if lists_sweep.connect is REAL_CONNECT:
    raise SystemExit("соединение не подменено")
print("@@" + json.dumps(out, ensure_ascii=False))
'''


def run() -> list[dict]:
    """Прогнать сценарии в отдельном процессе."""
    proc = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", CHILD.replace("ROOT_DIR", repr(str(ROOT)))],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("@@")), None)
    if line is None:
        raise SystemExit(f"прогон упал\n{proc.stderr[-3000:]}")
    return json.loads(line[2:])


def main(argv: list[str]) -> int:
    """--save — записать снимок; без флага — сравнить с ним."""
    now = run()
    if "--save" in argv:
        BASE.write_text(json.dumps(now, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"sql_snapshot saved: {len(now)} cases")
        return 0
    if not BASE.is_file():
        print("sql_snapshot: нет снимка tools/guard/baseline/sql-snapshot.json — снимите его через --save")
        return 1
    base = {c["case"]: c for c in json.loads(BASE.read_text(encoding="utf-8"))}
    changed = [c["case"] for c in now if base.get(c["case"]) != c] + [k for k in base if k not in {c["case"] for c in now}]
    for case in changed:
        print(f"== ИЗМЕНИЛСЯ SQL: {case}")
        print("   было ", json.dumps(base.get(case), ensure_ascii=False)[:900])
        print("   стало", json.dumps(next((c for c in now if c["case"] == case), None), ensure_ascii=False)[:900])
    print(f"sql_snapshot: {len(now)} cases, {len(changed)} changed")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
