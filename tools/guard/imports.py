"""
Сторож рефакторинга: импорты Python — без циклов, без правок sys.path и только вниз по слоям.

Автор: Чепела Даниил Максимович.

Граф строится по AST (код не исполняется). Импорт внутри функции тоже считается связью — именно такими «ленивыми»
импортами сейчас обходят циклы. Слои пакета core снизу вверх:
config ← classes ← functions ← auth ← api / admin / modules / static ← core.py ← __main__.py;
импорт вверх — ошибка. Для старого пакета server слоёв нет: только циклы и sys.path (база для сравнения).

Пока идёт рефакторинг, известные проблемы записаны в baseline/imports.json: сторож падает только на новых.
Исправили — запишите базу заново (--save), она только сжимается.

    python -B tools/guard/imports.py [core|server] [--save]
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAYERS = {"config": 0, "classes": 1, "functions": 2, "auth": 3, "api": 4, "admin": 4, "modules": 4, "static": 4,
          "core": 5, "__main__": 6}


def _modules(package: str) -> dict[str, Path]:
    """Имя модуля → файл, для всех модулей пакета."""
    base = ROOT / package
    out = {}
    for path in base.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = path
    return out


def _resolve(name: str, known: dict[str, Path]) -> str | None:
    """Самый длинный известный модуль, которым начинается имя импорта."""
    while name and name not in known:
        name = name.rpartition(".")[0]
    return name or None


def _edges(module: str, path: Path, known: dict[str, Path]) -> tuple[list[tuple[str, bool]], list[int]]:
    """Связи модуля (куда, лениво ли) и строки с правкой sys.path."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    edges, syspath = [], []
    top_level = {id(node) for node in tree.body}
    for node in ast.walk(tree):
        lazy = id(node) not in top_level
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (target := _resolve(alias.name, known)):
                    edges.append((target, lazy))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(parent + ([base] if base else []))
            for alias in node.names:
                if (target := _resolve(f"{base}.{alias.name}", known) or _resolve(base, known)):
                    edges.append((target, lazy))
        elif isinstance(node, ast.Attribute) and node.attr in ("insert", "append", "extend") \
                and isinstance(node.value, ast.Attribute) and node.value.attr == "path" \
                and isinstance(node.value.value, ast.Name) and node.value.value.id == "sys":
            syspath.append(node.lineno)
    return [(t, lazy) for t, lazy in edges if t != module], syspath


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Сильно связные компоненты больше одного модуля (алгоритм Тарьяна)."""
    index, low, stack, on_stack, out, counter = {}, {}, [], set(), [], [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in graph.get(node, ()):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            comp = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                comp.append(item)
                if item == node:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    sys.setrecursionlimit(10_000)
    for node in graph:
        if node not in index:
            visit(node)
    return out


def _layer(module: str) -> int | None:
    """Слой модуля пакета core по второй части имени (core.functions.presence → functions)."""
    parts = module.split(".")
    if parts[0] != "core":
        return None
    key = parts[1] if len(parts) > 1 else "core"
    return LAYERS.get(key)


def main(argv: list[str]) -> int:
    """Проверить пакет; выход 1 — новая проблема: цикл, sys.path или импорт вверх по слоям."""
    names = [a for a in argv if not a.startswith("--")]
    package = names[0] if names else ("core" if (ROOT / "core").is_dir() else "server")
    known = _modules(package)
    graph: dict[str, set[str]] = {m: set() for m in known}
    problems: list[str] = []
    lazy_count = 0
    for module, path in sorted(known.items()):
        edges, syspath = _edges(module, path, known)
        problems += [f"sys.path: {path.relative_to(ROOT).as_posix()}:{line}" for line in syspath]
        for target, lazy in edges:
            graph[module].add(target)
            lazy_count += lazy
            mine, theirs = _layer(module), _layer(target)
            if mine is not None and theirs is not None and theirs > mine:
                problems.append(f"ВВЕРХ ПО СЛОЯМ: {module} → {target}")
    problems += ["ЦИКЛ: " + " → ".join(comp) for comp in _cycles(graph)]
    problems = sorted(set(problems))  # одна и та же связь из нескольких функций — одна проблема
    base_file = Path(__file__).with_name("baseline") / "imports.json"
    if "--save" in argv:
        base_file.write_text(json.dumps(sorted(problems), ensure_ascii=False, indent=1), encoding="utf-8")
    known_problems = set(json.loads(base_file.read_text(encoding="utf-8"))) if base_file.is_file() else set()
    new = [p for p in problems if p not in known_problems]
    for line in problems:
        print(("НОВОЕ " if line in new else "известно ") + line)
    print(f"imports: {len(known)} modules, {sum(len(v) for v in graph.values())} links ({lazy_count} inside functions), "
          f"{len(problems)} problems, {len(new)} new")
    return 1 if new else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
