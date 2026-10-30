"""
Сторож рефакторинга: живой сервер отвечает на настоящие запросы так же, как до шага.

Автор: Чепела Даниил Максимович.

Набор GET-запросов к запущенному AsoPlay (127.0.0.1:8787) — гостем, временным пользователем и администратором
панели (подпись по контракту админ-API, секрет из .env), вместе с ответами-ошибками: 401, 404, 405, 422.
Сравниваются код ответа, нужные заголовки и тело. Тело JSON сравнивается по значениям, HTML и текст — по хешу,
а ответы с чужими или растущими данными (DATA: каталог MAL и Shikimori, карта сайта) — по строению.
Изменчивое (время сервера, аптайм, задержки источников) находится само: --save прогоняет набор дважды подряд
и помечает всё, что разошлось. Так шаги проверяет и движок Anime Social (сверка ответов «до/после»).

Пользователь — временный аккаунт проверки с заранее заданными данными (temp_account.py): создаётся прямо в базе
и удаляется после прогона. Админ — несуществующий id 999999999: панель проверяет только подпись, а запросы — одно
чтение. От имени настоящих людей проверка не действует, запросов на запись нет.
База снимается в начале шага и сравнивается в тот же день: календарь активности в профиле считается от сегодня.

    python -B tools/guard/responses.py --save   записать базу (baseline/responses.json); сервер должен работать
    python -B tools/guard/responses.py          сравнить; выход 1 — если ответ изменился
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).parent))  # только для инструмента: соседний модуль временного аккаунта
from temp_account import HANDLE, TempAccount  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).with_name("baseline") / "responses.json"
SITE = "http://127.0.0.1:8787"
ADMIN_ID = 999999999  # такого человека нет: подпись панели проверяется без базы
HEADERS = ("content-type", "cache-control", "location", "x-robots-tag", "content-security-policy", "retry-after")
# Параметры адреса, которые меняются при каждом запросе (PKCE в /auth/as/start)
VOLATILE_QUERY = re.compile(r"((?:state|code_challenge|nonce)=)[^&]+")
# Состояние процесса, а не кода: после перезапуска сервера (а сверка «до/после» всегда идёт через него) оно другое —
# время сборки списков adblock и запуска, счётчики и задержки источников, открытые вкладки. Ключи остаются
# в сравнении, значения — нет. «[]» — каждый элемент списка, путь без листа — всё под ним.
PROCESS_STATE = {
    "guest GET /health": ["adblock.updated_at", "adblock.refreshing", "source_health"],
    "guest GET /adblock/status": ["updated_at", "refreshing"],
    "guest GET /source-health": [""],
    "admin GET /api/admin/summary": ["data.started_at", "data.online", "data.health.sources[].latency_ms",
                                     "data.health.sources[].requests_24h", "data.health.sources[].fail_rate_24h",
                                     "data.health.sources[].ok"],
}
# Данные, которые меняются сами, без нашего кода: каталог MAL и Shikimori через прокси обновляется, когда истекает
# кеш, карта сайта прирастает тайтлами, которые кто-то открыл. У них сравнивается строение ответа: ключи JSON на всех
# уровнях, а у карты сайта — виды адресов и теги.
DATA = {"guest GET /proxy/jikan/anime/52991", "guest GET /proxy/jikan/anime/1535", "guest GET /shiki/anime/52991",
        "guest GET /shiki/batch?ids=52991,1535", "guest GET /sitemap.xml"}

GUEST = [
    "/health", "/sources", "/source-health", "/adblock/status", "/adblock/check?url=https://doubleclick.net/ad.js",
    "/adblock/check", "/reports/kinds", "/auth/config", "/auth/me", "/auth/as/whoami", "/auth/register",
    "/auth/as/start?next=/", "/account/favorites", "/account/lists", "/account/friends", "/src/search",
    "/src/episodes?key=nope", "/player/frame", "/proxy/img", "/proxy/jikan/anime/52991", "/proxy/jikan/anime/1535",
    "/shiki/anime/52991", "/shiki/batch?ids=52991,1535", "/sitemap.xml", "/robots.txt", "/", "/catalog", "/login",
    "/my/lists", "/top", "/anime/52991", "/anime/1535-death-note/", "/anime/99999999-nope/", f"/@{HANDLE}",
    "/@nobody-here-at-all", f"/profile/{HANDLE}/summary", f"/profile/{HANDLE}/activity",
    f"/profile/{HANDLE}/activity/recent", f"/profile/{HANDLE}/lists", "/no-such-page-anywhere",
]
USER = [
    "/auth/me", "/auth/as/whoami", "/account/favorites", "/account/history", "/account/ratings", "/account/dub-prefs",
    "/account/settings", "/account/privacy", "/account/lists", "/account/lists?status=watching", "/account/lists/counts",
    "/account/activity", "/account/activity/recent", "/account/import-marks", "/account/progress/52991",
    "/account/friends", "/account/friends/requests", "/account/friends/suggestions", "/account/friends/title/52991",
]
ADMIN = [
    "/api/admin/summary", "/api/admin/reports?status=open", "/api/admin/reports?status=fixed", "/api/admin/titles",
    "/api/admin/titles?q=1535", "/api/admin/settings", "/api/admin/audit", "/api/admin/no-such-method",
]
UNSIGNED = [("GET", "/api/admin/summary"), ("POST", "/api/admin/summary")]


def _admin_headers(secret: bytes, method: str, path: str) -> dict[str, str]:
    """Подпись по контракту: HMAC-SHA256(secret, ts\\nMETHOD\\npath?query\\nadmin_user\\nbody)."""
    ts = str(int(time.time()))
    message = f"{ts}\n{method}\n{path}\n{ADMIN_ID}\n".encode("utf-8")
    digest = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return {"X-AsoPlay-Admin": f"{ts}.{digest}", "X-AsoPlay-Admin-User": str(ADMIN_ID)}


def _shape(value):
    """Строение JSON: ключи словарей на всех уровнях, вместо значений и списков — метки."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    return "list" if isinstance(value, list) else "value"


def _sitemap(text: str) -> dict:
    """Строение карты сайта: виды адресов (страница тайтла — одним видом) и теги, без числа тайтлов."""
    locs = re.findall(r"<loc>([^<]+)</loc>", text)
    kinds = {re.sub(r"/anime/\d+-[^/]+/", "/anime/{id}-{slug}/", re.sub(r"^https?://[^/]+", "", loc)) for loc in locs}
    return {"kinds": sorted(kinds), "tags": sorted(set(re.findall(r"<(\w+)[ >]", text)))}


def _record(resp: httpx.Response, key: str = "") -> dict:
    """Код, нужные заголовки и тело: JSON — значением, остальное — хешем и длиной; ответы из DATA — строением."""
    headers = {k: resp.headers[k] for k in HEADERS if k in resp.headers}
    if "location" in headers:
        headers["location"] = VOLATILE_QUERY.sub(r"\1…", headers["location"])
    out = {"status": resp.status_code, "headers": headers}
    if key in DATA and "xml" in resp.headers.get("content-type", ""):
        out["sitemap"] = _sitemap(resp.text)
        return out
    if "json" in resp.headers.get("content-type", ""):
        try:
            body = resp.json()
            out["json"] = _shape(body) if key in DATA else body
            return out
        except ValueError:
            pass
    out["sha256"] = hashlib.sha256(resp.content).hexdigest()
    out["bytes"] = len(resp.content)
    return out


# Данные AsoPlay временного аккаунта — с одним и тем же временем, чтобы ответы не зависели от дня записи
_T1, _T2, _DAY = datetime(2026, 10, 1, 12, 0), datetime(2026, 10, 20, 18, 30), datetime(2026, 10, 20).date()
_SEED = [
    ("INSERT INTO aviev_user_lists (user_id, mal_id, status, status_source, is_favorite, title, poster_url, added_at, "
     "updated_at) VALUES (%s, %s, %s, %s, %s, %s, '', %s, %s)",
     [(52991, "watching", "auto", 0, "Фрирен", _T1, _T2), (1535, None, "manual", 1, "Тетрадь смерти", _T1, _T1),
      (20, "planned", "manual", 0, "Наруто", _T1, _T1)]),
    ("INSERT INTO aviev_watch_history (user_id, mal_id, last_episode, episode_seconds, episode_duration, episodes_total, "
     "title, poster_url, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, '', %s)",
     [(52991, 3, 700, 1440, 28, "Фрирен", _T2)]),
    ("INSERT INTO aviev_episode_progress (user_id, mal_id, episode_num, seconds, duration, updated_at) "
     "VALUES (%s, %s, %s, %s, %s, %s)", [(52991, 1, 1440, 1440, _T2), (52991, 2, 1440, 1440, _T2), (52991, 3, 700, 1440, _T2)]),
    ("INSERT INTO aviev_activity (user_id, day, kind, mal_id, meta, at) VALUES (%s, %s, %s, %s, %s, %s)",
     [(_DAY, "watch_start", 52991, "ep=1", _T2), (_DAY, "list_add", 20, "planned:manual", _T1), (_DAY, "rate", 1535, "", _T1)]),
    ("INSERT INTO aviev_title_ratings (user_id, mal_id, score, set_at) VALUES (%s, %s, %s, %s)", [(1535, 9, _T1)]),
    ("INSERT INTO aviev_dub_prefs (user_id, mal_id, dub_norm, updated_at) VALUES (%s, %s, %s, %s)",
     [(52991, "anilibria", _T1)]),
    ("INSERT INTO aviev_account_settings (user_id, autonext, auto_add_lists, settings_json, updated_at) "
     "VALUES (%s, %s, %s, %s, %s)", [(0, 1, '{"theme": "dark"}', _T1)]),
    ("INSERT INTO aviev_privacy (user_id, hide_lists, hide_activity, hide_presence, show_watching, updated_at) "
     "VALUES (%s, %s, %s, %s, %s, %s)", [(0, 0, 0, 1, _T1)]),
    ("INSERT INTO aviev_import_marks (user_id, kind, imported_at, n_items) VALUES (%s, %s, %s, %s)",
     [("favorites", _T1, 1)]),
]


def run() -> dict[str, dict]:
    """Один прогон всего набора."""
    secret = (dotenv_values(ROOT / ".env").get("AV_ADMIN_SECRET") or "").strip().encode("utf-8")
    out: dict[str, dict] = {}
    with httpx.Client(base_url=SITE, follow_redirects=False, timeout=60) as client, TempAccount(_SEED) as token:
        for path in GUEST:
            out[f"guest GET {path}"] = _record(client.get(path), f"guest GET {path}")
        for path in USER:
            out[f"user GET {path}"] = _record(client.get(path, cookies={"aviev_session": token}))
        for path in ADMIN:
            out[f"admin GET {path}"] = _record(client.get(path, headers=_admin_headers(secret, "GET", path)))
        for method, path in UNSIGNED:
            out[f"unsigned {method} {path}"] = _record(client.request(method, path))
    return out


def _leaves(value):
    """Все значения под узлом заменены меткой, ключи и длины списков — как были."""
    if isinstance(value, dict):
        return {k: _leaves(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_leaves(v) for v in value]
    return "<процесс>"


def _process(value, path: str):
    """Спрятать состояние процесса по пути вида a.b[].c (пустой путь — всё тело)."""
    if not path:
        return _leaves(value)
    head, _, rest = path.partition(".")
    each = head.endswith("[]")
    key = head[:-2] if each else head
    if not isinstance(value, dict) or key not in value:
        return value
    inner = value[key]
    if each and isinstance(inner, list):
        return {**value, key: [_process(item, rest) for item in inner]}
    return {**value, key: _process(inner, rest)}


def _without_process(key: str, record: dict) -> dict:
    """Запись ответа без состояния процесса (для запросов из PROCESS_STATE)."""
    if key not in PROCESS_STATE or "json" not in record:
        return record
    body = record["json"]
    for path in PROCESS_STATE[key]:
        body = _process(body, path)
    return {**record, "json": body}


def _mask(a, b):
    """Где два прогона разошлись — «<изменчиво>»; остальное — как в первом."""
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return {k: _mask(a[k], b[k]) for k in a}
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [_mask(x, y) for x, y in zip(a, b)]
    return a if a == b else "<изменчиво>"


def _apply(base, now):
    """Наложить маску базы на новый ответ: изменчивые места не сравниваются."""
    if base == "<изменчиво>":
        return "<изменчиво>"
    if isinstance(base, dict) and isinstance(now, dict):
        return {k: _apply(base[k], now[k]) if k in base else now[k] for k in now}
    if isinstance(base, list) and isinstance(now, list) and len(base) == len(now):
        return [_apply(x, y) for x, y in zip(base, now)]
    return now


def main(argv: list[str]) -> int:
    """--save — два прогона и база с маской изменчивого; без флага — сравнить."""
    if "--save" in argv:
        first = run()
        time.sleep(2)
        second = run()
        base = {key: _without_process(key, _mask(first[key], second[key])) for key in first}
        BASE.write_text(json.dumps(base, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        volatile = [k for k in base if "<изменчиво>" in json.dumps(base[k], ensure_ascii=False)]
        print(f"responses saved: {len(base)} requests, volatile parts in {len(volatile)}: {', '.join(volatile)}")
        return 0
    if not BASE.is_file():
        print("responses: нет базы tools/guard/baseline/responses.json — снимите её через --save на коде до шага")
        return 1
    base = json.loads(BASE.read_text(encoding="utf-8"))
    now = run()
    changed = []
    for key in sorted(set(base) | set(now)):
        a = _without_process(key, base[key]) if key in base else None
        b = _without_process(key, _apply(base[key], now[key])) if key in now and key in base else now.get(key)
        if a != b:
            changed.append(key)
            print(f"== ИЗМЕНИЛСЯ ОТВЕТ: {key}")
            print("   было ", json.dumps(a, ensure_ascii=False, sort_keys=True)[:600])
            print("   стало", json.dumps(b, ensure_ascii=False, sort_keys=True)[:600])
    print(f"responses: {len(now)} requests, {len(changed)} changed")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
