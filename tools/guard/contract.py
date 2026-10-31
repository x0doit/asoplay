"""
Сторож рефакторинга: контракт HTTP и WebSocket не меняется.

Автор: Чепела Даниил Максимович.

Снимает с собранного приложения OpenAPI (пути, методы, параметры, тела, ответы, схемы моделей) и маршруты вне схемы:
WebSocket, HTML-страницы и примонтированную статику, а ещё точный порядок регистрации маршрутов. Имена моделей нормализуются: FastAPI добавляет к одинаковым
именам путь модуля (server__account_api__SettingsIn), а модули при рефакторинге переезжают. Поэтому модель
называется своим именем и хешем содержимого (SettingsIn@1a2b3c4d): две разные SettingsIn — панели и аккаунта —
не сливаются в одну, и ссылка на модель не может незаметно перейти на другую. Статика сравнивается отдельно
и ошибкой не считается — её адреса меняются по плану.

    python -B tools/guard/contract.py --save   записать базу (tools/guard/baseline/contract.json)
    python -B tools/guard/contract.py          сравнить с базой; выход 1 — если контракт изменился
    python -B tools/guard/contract.py --save --root <копия> --app server.main
                                               снять базу с другой копии кода (например, с коммита до шага)
"""
from __future__ import annotations

import difflib
import hashlib
import importlib
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).with_name("baseline") / "contract.json"
MODEL = re.compile(r"^(?:[A-Za-z0-9_]+__)+")
# Не поведение: operationId — внутреннее имя FastAPI (у маршрутов с несколькими методами ещё и меняется от запуска
# к запуску), description — текст docstring, который по плану переписывается при делении файлов
SKIP_KEYS = {"operationId", "description"}


def _arg(argv: list[str], name: str, default: str) -> str:
    """Значение флага --name value или значение по умолчанию."""
    return argv[argv.index(name) + 1] if name in argv else default


def _app(root: Path, module: str):
    """Собранное приложение FastAPI из копии кода root."""
    sys.path.insert(0, str(root))  # только для инструмента: сам код приложения так не запускается
    logging.disable(logging.CRITICAL)
    return importlib.import_module(module).app


def _norm(value):
    """Убрать из имён моделей и ссылок на них путь модуля, а заодно ключи SKIP_KEYS."""
    if isinstance(value, dict):
        return {MODEL.sub("", k) if isinstance(k, str) else k: _norm(v) for k, v in value.items() if k not in SKIP_KEYS}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str) and value.startswith("#/components/schemas/"):
        return "#/components/schemas/" + MODEL.sub("", value.rsplit("/", 1)[1])
    if isinstance(value, str):
        return MODEL.sub("", value) if "__" in value else value
    return value


def _included(route, prefix: str) -> tuple[list, str]:
    """Маршруты вложенного роутера и их префикс: FastAPI держит у части её собственные пути, а префикс входа
    (prefix у include_router или у роутера-входа) — в include_context; при спуске префиксы складываются."""
    return route.original_router.routes, prefix + (getattr(route.include_context, "prefix", "") or "")


def _walk(routes, out, prefix: str = ""):
    """Маршруты вне OpenAPI: WebSocket, скрытые из схемы, статика."""
    for route in routes:
        name = type(route).__name__
        if name == "_IncludedRouter":
            inner, inner_prefix = _included(route, prefix)
            _walk(inner, out, inner_prefix)
        elif "WebSocket" in name:
            out["websocket"].append(prefix + route.path)
        elif name == "Mount":
            out["static"].append(prefix + route.path)
        elif name in ("APIRoute", "Route") and re.search(r"\.(?:js|css)$", route.path):
            out["static"].append(prefix + route.path)  # файлы темы (/app.js, /styles.css) — переезжают по плану
        elif name == "APIRoute" and not route.include_in_schema:
            out["hidden"].append(f"{','.join(sorted(route.methods))} {prefix + route.path}")
        elif name == "Route":
            out["hidden"].append(f"{','.join(sorted(route.methods or []))} {prefix + route.path}")


def _model_keys(schemas: dict) -> dict[str, str]:
    """Полное имя модели → «имя@хеш содержимого» (содержимое — с нормализованными ссылками)."""
    out = {}
    for name, schema in schemas.items():
        body = json.dumps(_norm(schema), sort_keys=True, ensure_ascii=False)
        out[name] = f"{MODEL.sub('', name)}@{hashlib.sha1(body.encode('utf-8')).hexdigest()[:8]}"
    return out


def _refs(value, keys: dict[str, str]):
    """Заменить ссылки на модели их однозначными именами, остальное — как в _norm."""
    if isinstance(value, dict):
        return {MODEL.sub("", k) if isinstance(k, str) else k: _refs(v, keys)
                for k, v in value.items() if k not in SKIP_KEYS}
    if isinstance(value, list):
        return [_refs(v, keys) for v in value]
    if isinstance(value, str) and value.startswith("#/components/schemas/"):
        return "#/components/schemas/" + keys[value.rsplit("/", 1)[1]]
    return _norm(value)


def _order(routes, out: list[str], prefix: str = "") -> list[str]:
    """Порядок регистрации маршрутов (без статики) с полными путями: при делении роутеров он решает, какой маршрут
    ответит — /account/lists/counts должен стоять раньше /account/lists/{mal_id}."""
    for route in routes:
        name = type(route).__name__
        if name == "_IncludedRouter":
            inner, inner_prefix = _included(route, prefix)
            _order(inner, out, inner_prefix)
        elif "WebSocket" in name:
            out.append(f"WS {prefix + route.path}")
        elif name in ("APIRoute", "Route") and not re.search(r"\.(?:js|css)$", route.path):
            out.append(f"{','.join(sorted(route.methods or []))} {prefix + route.path}")
    return out


def snapshot(root: Path = ROOT, module: str = "core.core") -> dict:
    """Контракт приложения как JSON с упорядоченными ключами."""
    app = _app(root, module)
    raw = app.openapi()
    schemas = raw.get("components", {}).get("schemas", {})
    keys = _model_keys(schemas)
    extra = {"websocket": [], "hidden": [], "static": []}
    _walk(app.routes, extra)
    return {
        "paths": _refs(raw.get("paths", {}), keys),
        "schemas": sorted(json.dumps({keys[k]: _refs(v, keys)}, sort_keys=True, ensure_ascii=False)
                          for k, v in schemas.items()),
        "websocket": sorted(extra["websocket"]),
        "hidden": sorted(extra["hidden"]),
        "static": sorted(extra["static"]),
        "order": _order(app.routes, []),
    }


def main(argv: list[str]) -> int:
    """--save — записать базу; без флага — сравнить. --root и --app — снять с другой копии кода."""
    now = snapshot(Path(_arg(argv, "--root", str(ROOT))).resolve(), _arg(argv, "--app", "core.core"))
    if "--save" in argv:
        BASE.parent.mkdir(exist_ok=True)
        BASE.write_text(json.dumps(now, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        print(f"contract saved: {len(now['paths'])} paths, {len(now['websocket'])} websocket, "
              f"{len(now['hidden'])} hidden, {len(now['static'])} static, {len(now['order'])} routes in order")
        return 0
    base = json.loads(BASE.read_text(encoding="utf-8"))
    changed = 0
    for key in ("paths", "schemas", "websocket", "hidden", "order"):
        a = json.dumps(base[key], ensure_ascii=False, indent=1, sort_keys=True).splitlines()
        b = json.dumps(now[key], ensure_ascii=False, indent=1, sort_keys=True).splitlines()
        if a != b:
            changed += 1
            print(f"== КОНТРАКТ ИЗМЕНИЛСЯ: {key}")
            print("\n".join(list(difflib.unified_diff(a, b, lineterm="", n=1))[:80]))
    if base["static"] != now["static"]:
        print(f"static (по плану): {base['static']} → {now['static']}")
    print(f"contract: {len(now['paths'])} paths, {changed} sections changed")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
