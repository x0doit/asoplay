"""
Все сторожа рефакторинга одной командой — после каждого шага.

Автор: Чепела Даниил Максимович.

Быстрые: длины файлов, импорты, docblock, контракт, объявления Python, прокси плеера. С --full ещё проверки ядра
(core/tests, каждый раздел — в своём процессе) и фронтенда (design/EDDAS/tests), ответы живого сервера, снимок
вычисленных стилей и браузерный QA (tools/qa): им нужен запущенный локальный сервер, Chrome и playwright-core
(tools/guard/browser.cjs), а ответам и стилям — база, снятая через --save (core/tests/README.md).
Выход 1 — если хоть один сторож упал.

    python -B tools/guard/all.py [--full]
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
QUICK = [
    ("lines", [PY, "-B", "tools/guard/lines.py"]),
    ("imports", [PY, "-B", "tools/guard/imports.py"]),
    ("docblocks", [PY, "-B", "tools/guard/docblocks.py"]),
    ("contract", [PY, "-B", "tools/guard/contract.py"]),
    ("decls", [PY, "-B", "tools/guard/decls.py"]),
    ("player", [PY, "-B", "tools/guard/player_snapshot.py"]),
]


def _tests() -> list[tuple[str, list[str]]]:
    """Проверки ядра — по разделу, каждый в своём процессе (core/tests/run.py), и фронтенда (Node)."""
    sections = sorted(p.stem.removeprefix("test_") for p in (ROOT / "core" / "tests").glob("test_*.py"))
    runs = [(f"py:{name}", [PY, "-B", "core/tests/run.py", name]) for name in sections]
    node_files = sorted((ROOT / "design" / "EDDAS" / "tests").glob("*.test.cjs"))
    runs.append(("node", ["node", "--test", *map(str, node_files)]))
    return runs


def main(argv: list[str]) -> int:
    """Прогнать сторожей; --full — ещё проверки, стили и QA."""
    steps = QUICK + (_tests() + [("responses", [PY, "-B", "-W", "ignore", "tools/guard/responses.py"]),
                                 ("styles", ["node", "tools/guard/styles.cjs"]),
                                 ("qa", ["node", "tools/qa/eddas-qa.cjs"])] if "--full" in argv else [])
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed = []
    for name, cmd in steps:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        lines = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
        # Итоговая строка сторожа («contract: …»), а не предупреждения библиотек
        tail = [line for line in lines if line.split(":")[0].isidentifier() and "warn" not in line.lower()][-1:] or lines[-1:] or [""]
        status = "ok  " if proc.returncode == 0 else "FAIL"
        print(f"{status} {name:28} {tail[0][:110]}")
        if proc.returncode:
            failed.append(name)
    print(f"guards: {len(steps) - len(failed)}/{len(steps)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
