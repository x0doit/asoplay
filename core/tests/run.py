"""
Запуск проверок ядра: все разделы или названные, каждый файл — в своём процессе.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Раздел выполняется как python -m core.tests.test_{раздел} из корня проекта. Корень кладётся в путь импорта явно:
встраиваемый Python (OSPanel, python313._pth) не добавляет туда текущую папку даже для -m.

    python -B core/tests/run.py                         все разделы
    python -B core/tests/run.py admin_api presence      только эти
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
# То же, что python -m <модуль> из корня; аргументы запуска unittest не достаются
CHILD = ("import runpy, sys; root, name = sys.argv[1:]; del sys.argv[1:]; sys.path.insert(0, root); "
         "runpy.run_module(name, run_name='__main__', alter_sys=True)")


def sections(names: list[str]) -> list[str]:
    """Модули проверок: названные или все test_*.py по алфавиту."""
    found = sorted(p.stem for p in HERE.glob("test_*.py"))
    wanted = [n if n.startswith("test_") else f"test_{n}" for n in names] or found
    missing = [n for n in wanted if n not in found]
    if missing:
        raise SystemExit(f"нет таких разделов: {', '.join(missing)}")
    return wanted


def run(name: str) -> tuple[bool, int]:
    """Прогнать один раздел; итог — строкой «раздел: OK, проверок: N», при сбое — весь вывод."""
    proc = subprocess.run([sys.executable, "-B", "-c", CHILD, str(ROOT), f"core.tests.{name}"], cwd=ROOT,
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout + proc.stderr).rstrip()
    ran = re.search(r"^Ran (\d+) tests?", out, re.M)
    count = int(ran.group(1)) if ran else 0
    status = out.splitlines()[-1] if out else ""
    if proc.returncode:
        print(out)
    print(f"{name}: {status if status.startswith(('OK', 'FAILED')) else 'FAILED'}, проверок: {count}")
    return proc.returncode == 0, count


def main(argv: list[str]) -> int:
    """Прогнать разделы; выход 1 — если хоть один упал."""
    # В файл или пайп Windows пишет в кодировке системы (cp1251) — кириллица ломается; вывод всегда в UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    names = sections(argv)
    results = [run(name) for name in names]
    if len(names) > 1:
        passed = sum(ok for ok, _ in results)
        print(f"tests: {passed}/{len(names)} разделов, проверок: {sum(n for _, n in results)}")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
