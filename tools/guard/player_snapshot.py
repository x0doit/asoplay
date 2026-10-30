"""
Сторож рефакторинга: прокси плеера отдаёт те же байты, что до переноса.

Автор: Чепела Даниил Максимович.

Прогоняет набор образцов через то, что прокси плеера собирает сам, без сети: чистые фреймы (CVH, Sibnet, VK),
мост во фрейме для Kodik, Alloha и прочих, переписывание HTML, CSS, m3u8 и JS, страницу ошибки CVH и файлы
/player/asoplay-shield.js и /player/kodik-skin.css. Сравниваются хеши байтов ответа и нужные заголовки. Функции
ищутся по таблице FUNCS: сначала там, где они лежат до деления, затем в новых модулях — при переименовании правится
только таблица.

    python -B tools/guard/player_snapshot.py --save   записать базу
    python -B tools/guard/player_snapshot.py          сравнить; выход 1 — если хоть один ответ изменился
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).with_name("baseline") / "player.json"
FUNCS = {
    "clean_video_frame": ["core.modules.player.routes:_clean_video_frame", "core.modules.player.frame:clean_video_frame"],
    "cvh_error_frame": ["core.modules.player.routes:_cvh_error_frame", "core.modules.player.cvh:error_frame"],
    "bridge_script": ["core.modules.player.routes:_bridge_script", "core.modules.player.frame:bridge_script"],
    "rewrite_body": ["core.modules.player.routes:_rewrite_body", "core.modules.player.rewrite:rewrite_body"],
}
HTML = (b'<!doctype html><html><head><script src="/js/player.js"></script><link rel="stylesheet" href="css/a.css">'
        b'<script>var u="https://cdn.example/v.m3u8";</script></head><body><iframe src="//other.example/e"></iframe>'
        b'<img src="/p.png"><video poster="/poster.jpg"></video></body></html>')
CSS = b'.a{background:url(/img/bg.png)} .b{background:url("../x.svg")} @import "theme.css";'
M3U8 = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\n720/index.m3u8\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\nseg1.ts\n"
JS = b'fetch("https://plapi.cdnvideohub.com/api/v1/player?x=1");var s="/assets/a.js";import("./mod.js");'
CASES = [
    ("clean_video_frame", "sibnet", ("https://video.sibnet.ru/v/abc.mp4", "https://video.sibnet.ru/shell.php", "https://video.sibnet.ru/p.jpg"),
     {"element_id": "av-sibnet-video"}),
    ("clean_video_frame", "vk", ("https://vk.example/master.m3u8", "https://vk.com/"),
     {"element_id": "av-vk-video", "sources": [{"label": "720p", "url": "https://vk.example/720.m3u8"}, {"label": "480p", "url": "https://vk.example/480.m3u8"}]}),
    ("clean_video_frame", "cvh", ("https://cdn.cvh.example/hls/master.m3u8", "https://player.cdnvideohub.com/s2/stable/video.html", "https://cdn.cvh.example/p.jpg"),
     {"sources": [{"label": "Auto", "url": "https://cdn.cvh.example/hls/master.m3u8"}]}),
    ("cvh_error_frame", "message", ("Видео недоступно <b>сейчас</b>",), {}),
    ("bridge_script", "kodik", ("https://kodikplayer.com/seria/1/abc/720p",), {}),
    ("bridge_script", "alloha", ("https://stloadi.live/?token_movie=abc",), {}),
    ("bridge_script", "cvh", ("https://player.cdnvideohub.com/s2/stable/video.html",), {}),
    ("bridge_script", "generic", ("https://player.example.org/embed/1",), {}),
    ("rewrite_body", "html", (HTML, "text/html; charset=utf-8", "https://player.example.org/embed/1"), {}),
    ("rewrite_body", "frame", (HTML, "text/html", "https://kodikplayer.com/seria/1/abc/720p"), {"frame": True}),
    ("rewrite_body", "css", (CSS, "text/css", "https://player.example.org/css/main.css"), {}),
    ("rewrite_body", "m3u8", (M3U8, "application/vnd.apple.mpegurl", "https://cdn.example/hls/master.m3u8"), {"referer": "https://player.example.org/"}),
    ("rewrite_body", "js-cvh", (JS, "application/javascript", "https://player.cdnvideohub.com/s2/stable/app.js"), {}),
    ("rewrite_body", "js", (JS, "text/javascript", "https://player.example.org/js/app.js"), {}),
]
ROUTES = ["/player/asoplay-shield.js", "/player/kodik-skin.css"]
HEADERS = ("content-type", "cache-control", "content-security-policy", "x-frame-options", "referrer-policy")


def _func(name: str):
    """Функция по таблице FUNCS: из старого модуля или из нового."""
    for ref in FUNCS[name]:
        module, attr = ref.split(":")
        try:
            return getattr(importlib.import_module(module), attr)
        except (ModuleNotFoundError, AttributeError):
            continue
    raise SystemExit(f"{name}: not found in {FUNCS[name]}")


def _digest(value) -> dict:
    """Хеш байтов и нужные заголовки ответа (Response, bytes или str)."""
    headers = {}
    if hasattr(value, "body"):
        headers = {k: v for k, v in value.headers.items() if k in HEADERS}
        value = value.body
    data = value.encode("utf-8") if isinstance(value, str) else value
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "headers": headers}


def snapshot() -> dict:
    """Все образцы: имя → хеш и заголовки."""
    sys.path.insert(0, str(ROOT))  # только для инструмента
    logging.disable(logging.CRITICAL)
    out = {f"{name}/{case}": _digest(_func(name)(*args, **kwargs)) for name, case, args, kwargs in CASES}
    from fastapi.testclient import TestClient
    client = TestClient(importlib.import_module("core.core").app)
    for path in ROUTES:
        resp = client.get(path)
        out[f"GET {path}"] = {"status": resp.status_code, **_digest(resp.content),
                              "headers": {k: v for k, v in resp.headers.items() if k in HEADERS}}
    return out


def main(argv: list[str]) -> int:
    """--save — записать базу; без флага — сравнить."""
    now = snapshot()
    if "--save" in argv:
        BASE.parent.mkdir(exist_ok=True)
        BASE.write_text(json.dumps(now, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        print(f"player snapshot saved: {len(now)} cases")
        return 0
    base = json.loads(BASE.read_text(encoding="utf-8"))
    changed = [key for key in sorted(set(base) | set(now)) if base.get(key) != now.get(key)]
    for key in changed:
        print(f"ИЗМЕНИЛСЯ ОТВЕТ: {key}: {base.get(key)} → {now.get(key)}")
    print(f"player: {len(now)} cases, {len(changed)} changed")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
