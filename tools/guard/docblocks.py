"""
Сторож рефакторинга: у каждого модуля и каждой функции — docblock, у модуля — автор.

Автор: Чепела Даниил Максимович.

Проверяет только код новой структуры: Python в core/ (docstring модуля с автором, docstring у каждой функции
и класса, кроме вложенных однострочных) и JavaScript в design/EDDAS/js/ (комментарий-заголовок файла с автором
и /** … */ перед каждой функцией, классом и методом верхнего уровня). Старые файлы (js/, app.js) и файлы
из pending.txt, которые ещё ждут своего шага, не проверяются.

    python -B tools/guard/docblocks.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # только для инструмента: соседний сторож с pending.txt
from lines import _pending  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
AUTHOR = "Чепела Даниил Максимович"
JS_DEF = re.compile(r"^(?:export\s+)?(?:async\s+)?(?:function\s*\*?\s*[\w$]+|class\s+[\w$]+)|^  (?:async\s+)?(?!if\b|for\b|while\b|switch\b|catch\b)[\w$]+\s*\([^)]*\)\s*\{\s*$")


def _python() -> list[str]:
    """Нарушения в core/**/*.py."""
    out = []
    pending = _pending()
    for path in sorted((ROOT / "core").rglob("*.py")) if (ROOT / "core").is_dir() else []:
        if {"__pycache__", "vendor", "tests"} & set(path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in pending:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = ast.get_docstring(tree) or ""
        if AUTHOR not in doc:
            out.append(f"{rel}: нет docstring модуля с автором")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not ast.get_docstring(node):
                out.append(f"{rel}:{node.lineno}: {node.name} — нет docstring")
    return out


def _javascript() -> list[str]:
    """Нарушения в design/EDDAS/js/**/*.js (кроме сторонних библиотек и проверок)."""
    out = []
    base = ROOT / "design" / "EDDAS" / "js"
    for path in sorted(base.rglob("*.js")) if base.is_dir() else []:
        if {"lib", "tests", "vendor"} & set(path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in _pending():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if AUTHOR not in "\n".join(lines[:15]):
            out.append(f"{rel}: нет заголовка файла с автором")
        for i, line in enumerate(lines):
            if JS_DEF.match(line):
                above = next((lines[j].strip() for j in range(i - 1, -1, -1) if lines[j].strip()), "")
                if not above.endswith("*/"):
                    out.append(f"{rel}:{i + 1}: {line.strip()[:60]} — нет /** … */")
    return out


def main() -> int:
    """Выход 1 — если хоть у одного модуля или функции нет docblock."""
    problems = _python() + _javascript()
    for line in problems:
        print(line)
    print(f"docblocks: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
