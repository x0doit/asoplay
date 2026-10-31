"""
Сторож рефакторинга: объявления Python не пропадают и не меняются при переносе и делении файлов.

Автор: Чепела Даниил Максимович.

Слепок всех объявлений: функции модуля и методы классов с параметрами (имена, значения по умолчанию, аннотации,
декораторы), классы с базами, присваивания модуля (константы, логгеры, роутеры) — с хешем значения. Ключ — имя без
модуля: файлы при рефакторинге переезжают и делятся, а объявления должны остаться теми же. Одинаковые имена
в разных модулях (router, startup, log) хранятся списком. Тела функций не сравниваются — это делают проверки
и снимки ответов. Так же шаги проверяет движок Anime Social.

Порядок работы: --save в начале шага, сравнение в конце. Каждое расхождение должно объясняться шагом; после
разбора база записывается заново.

    python -B tools/guard/decls.py --save [--root <копия кода>]   записать базу (baseline/decls.json)
    python -B tools/guard/decls.py                               сравнить; выход 1 — если что-то пропало,
                                                                 появилось или изменилось
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).with_name("baseline") / "decls.json"
SKIP = {"__pycache__", "vendor", "tests"}


def _short(text: str) -> str:
    """Короткий хеш длинного значения."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str = "") -> tuple[str, str]:
    """Имя и подпись функции: декораторы, async, параметры, аннотация результата."""
    deco = "".join(f"@{ast.unparse(d)} " for d in node.decorator_list)
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return prefix + node.name, f"{deco}{kind}({ast.unparse(node.args)}){returns}"


def _statements(tree: ast.Module):
    """Инструкции верхнего уровня, включая тела if/try/with на верхнем уровне."""
    for node in tree.body:
        yield node
        if isinstance(node, (ast.If, ast.Try, ast.With, ast.For)):
            for part in ("body", "orelse", "finalbody"):
                yield from getattr(node, part, [])
            for handler in getattr(node, "handlers", []):
                yield from handler.body


def _declarations(path: Path) -> list[tuple[str, str]]:
    """Объявления одного модуля: (имя, описание)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in _statements(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_function(node))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases + node.keywords)
            out.append((node.name, f"class({bases})"))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(_function(item, f"{node.name}."))
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    value = ast.unparse(item.value) if item.value else ""
                    out.append((f"{node.name}.{item.target.id}", f"{ast.unparse(item.annotation)} = {_short(value)}"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = ast.unparse(node.value) if node.value is not None else ""
            for target in targets:
                # Присваивание атрибуту другого модуля (realtime.extra_origins = …) — полным именем
                names = [ast.unparse(target)] if isinstance(target, ast.Attribute) else                     [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]
                out += [(name, f"= {_short(value)}") for name in names]
    return out


def snapshot(root: Path) -> dict[str, list[str]]:
    """Имя → отсортированный список описаний по всем модулям пакета (core, а до шага 2 — server)."""
    package = root / ("core" if (root / "core").is_dir() else "server")
    found: dict[str, list[str]] = defaultdict(list)
    for path in sorted(package.rglob("*.py")):
        if SKIP & set(path.relative_to(root).parts):
            continue
        for name, desc in _declarations(path):
            found[name].append(desc)
    return {name: sorted(descs) for name, descs in sorted(found.items())}


def main(argv: list[str]) -> int:
    """--save — записать базу (с --root — с другой копии кода); без флага — сравнить."""
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv else ROOT
    now = snapshot(root)
    if "--save" in argv:
        BASE.write_text(json.dumps(now, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"decls saved: {len(now)} names, {sum(map(len, now.values()))} declarations")
        return 0
    base = json.loads(BASE.read_text(encoding="utf-8"))
    gone = sorted(set(base) - set(now))
    new = sorted(set(now) - set(base))
    changed = sorted(n for n in set(base) & set(now) if base[n] != now[n])
    for name in gone:
        print(f"ПРОПАЛО: {name} {base[name]}")
    for name in new:
        print(f"ПОЯВИЛОСЬ: {name} {now[name]}")
    for name in changed:
        print(f"ИЗМЕНИЛОСЬ: {name}\n   было {base[name]}\n   стало {now[name]}")
    print(f"decls: {len(now)} names, {len(gone)} gone, {len(new)} new, {len(changed)} changed")
    return 1 if gone or new or changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
