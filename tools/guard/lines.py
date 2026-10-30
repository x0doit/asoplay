"""
Сторож рефакторинга: файлы приложения и его проверок — не длиннее 300 строк.

Автор: Чепела Даниил Максимович.

Исключения без обоснования: сторонние библиотеки, сгенерированное, SQL, документация, шрифты и картинки, общие файлы
EDDAS (копии из Anime Social — делятся вместе с ней), снимки сторожей. Файлы, которые ещё ждут своего шага,
перечислены в pending.txt рядом; по мере шагов список пустеет. Выход 1 — если что-то длиннее предела и не исключено.

    python -B tools/guard/lines.py            проверить
    python -B tools/guard/lines.py --report   все файлы длиннее 200 строк, без ошибки
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 300
TARGET = 200
# Где лежит код приложения — и до рефакторинга, и после; сторожа и QA — тоже код
ROOTS = ("core", "server", "design/EDDAS", "js", "assets", "app.js", "styles.css", "index.html",
         "tools/guard", "tools/qa")
TEXT = {".py", ".js", ".mjs", ".cjs", ".css", ".html", ".php", ".json"}
# Не код приложения или не наш: сторонние библиотеки, сгенерированное, снимки сторожей, общие файлы EDDAS
SKIP_PARTS = {"vendor", "__pycache__", "node_modules", "fonts", "cache", "baseline"}
SKIP_NAMES = {"composer.lock", "composer.json"}
EDDAS_COPIES = {"tokens.css", "eddas.css", "app.css", "fontawesome.css"}


def _files() -> list[Path]:
    """Текстовые файлы приложения из ROOTS, без исключённых."""
    out: list[Path] = []
    for name in ROOTS:
        path = ROOT / name
        items = [path] if path.is_file() else path.rglob("*") if path.is_dir() else []
        for item in items:
            rel = item.relative_to(ROOT)
            if not item.is_file() or item.suffix not in TEXT or item.name in SKIP_NAMES:
                continue
            if SKIP_PARTS & set(rel.parts):
                continue
            if item.suffix == ".css" and item.name in EDDAS_COPIES and {"eddas", "EDDAS"} & set(rel.parts):
                continue
            out.append(item)
    return sorted(out)


def _pending() -> set[str]:
    """Файлы, которые ещё ждут своего шага (pending.txt: по пути на строку, # — комментарий)."""
    path = Path(__file__).with_name("pending.txt")
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.split("#")[0].strip() for line in lines if line.split("#")[0].strip()}


def main(argv: list[str]) -> int:
    """Проверить длины; --report — показать всё длиннее TARGET и выйти без ошибки."""
    report = "--report" in argv
    pending = _pending()
    over, waiting = [], []
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if report and count > TARGET:
            print(f"{count:6}  {rel}")
        if count > LIMIT:
            (waiting if rel in pending else over).append((count, rel))
    if report:
        return 0
    for count, rel in sorted(over, reverse=True):
        print(f"ДЛИННЕЕ {LIMIT}: {count:6}  {rel}")
    print(f"lines: {len(over)} over the limit, {len(waiting)} still waiting for their step")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
