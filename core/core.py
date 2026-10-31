"""
Сборка AsoPlay на FastAPI: приложение, CORS, жизненный цикл, связи слоёв, роутеры, обработчики ошибок, статика.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Запуск — python -m core (core/__main__.py). Первым делом env.load(): настройки в core/config/config.py читают
окружение при импорте. Маршруты живут в разделах core/api/sections, страницы — в core/modules, админ-API —
в core/admin; здесь они подключаются в том порядке, от которого зависит, какой маршрут выигрывает. Связи слоёв, которые
нельзя импортировать снизу вверх, core.py задаёт явными строками до первого запроса. Статика и оболочка SPA переезжают
в core/static.py на шаге 6 рефакторинга.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

log = logging.getLogger("asoplay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from core.config import env

env.load()  # до остальных импортов: настройки в core/config/config.py читают окружение при импорте

# VPN must not start during import — that was the old bug. We arm it from the
# FastAPI startup event below. Importing the module is cheap and side-effect
# free.
from core.admin import auth as admin_auth, router as admin_router, summary as admin_summary
from core.api.sections import account, activity, auth, catalog, friends, health, lists, messages, profile, social_login
from core.api.sections import random as random_api, realtime as realtime_ws, sources as source_api
from core.classes import http, http_cache
from core.classes.adblock import engine as adblock
from core.classes.jikan import catalog as jikan_catalog
from core.classes.vpn import bridge as vpn_bridge
from core.config import config
from core.functions import (
    lists_sweep, presence_batch, realtime, realtime_social, site_settings, sources, video_reports,
)
from core.modules import profile_pages, seo, title_pages
from core.modules.player import routes as player_proxy


from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


# ---------- app ----------
log.info("CORS allowed origins: %s", config.ALLOWED_ORIGINS)

app = FastAPI(title="AsoPlay Backend", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.middleware("http")
async def _player_proxy_null_origin_cors(request: Request, call_next):
    """CORS для плееров в песочнице: их запросы идут с Origin: null — разрешено только /player/proxy и
    /player/cvh-api."""
    # Proxied players run inside a sandbox without allow-same-origin. Browser
    # fetch/XHR requests from that frame therefore use Origin: null. Keep this
    # exception scoped to /player/proxy so account/auth APIs are not exposed to
    # arbitrary opaque-origin documents.
    if (
        (request.url.path.startswith("/player/proxy") or request.url.path.startswith("/player/cvh-api"))
        and request.headers.get("origin") == "null"
    ):
        if request.method == "OPTIONS":
            return Response(headers={
                "Access-Control-Allow-Origin": "null",
                "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Max-Age": "600",
            })
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "null"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
        return response
    return await call_next(request)


# ---------- lifecycle ----------
@app.on_event("startup")
async def _startup() -> None:
    """Запуск: мост VPN и его сторож, решения панели, каталог, реклама, плеер, пакет присутствия,
    realtime, правило 30 дней."""
    # Start xray here, not at import time — tests and tooling can now import
    # the server module without spawning processes.
    if vpn_bridge.activate():
        log.info("VPN bridge active via %s", vpn_bridge.VPN_PROXY_URL)
    else:
        log.info("VPN bridge not active — falling back to direct outbound")
    vpn_bridge.start_watchdog()
    admin_summary.STARTED_AT = time.time()
    await asyncio.to_thread(site_settings.state.load)
    await jikan_catalog.startup()
    await adblock.startup()
    await player_proxy.startup()
    await presence_batch.startup()
    await realtime.startup()
    await lists_sweep.startup()


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Остановка в обратном порядке; клиенты источников через мост VPN закрывает sources.shutdown()."""
    await lists_sweep.shutdown()
    await realtime.shutdown()
    await presence_batch.shutdown()
    await player_proxy.shutdown()
    await adblock.shutdown()
    await jikan_catalog.shutdown()
    await http_cache.shutdown()
    await http.shutdown()
    await sources.shutdown()
    vpn_bridge.shutdown()


# ---------- связи слоёв ----------
# Вкладкам по живому соединению (/ws): то же, что им нужно из /health, без запроса в базу, и то, что
# решили в панели Anime Social (плашка, скрытые и 18+ тайтлы, способы входа) — правка видна сразу
realtime_social.status_provider = lambda: {
    "ok": True,
    "sources": sources.player_sources(),
    "vpn": vpn_bridge.is_active(),
    "site": site_settings.public_state(),
}
realtime_ws.extra_origins = {o.rstrip("/") for o in config.ALLOWED_ORIGINS if o != "*"}
site_settings.listeners.append(lambda _what: realtime.status_changed())
admin_summary.loaded_sources = sources.all_sources
# Каталог (core/classes/jikan) ниже site_settings и не импортирует его: скрытые по жалобе правообладателя
# тайтлы он берёт из функций, которые core.py регистрирует здесь, до первого запроса.
jikan_catalog.register_title_filter(hidden=site_settings.hidden, visible=site_settings.visible)


# ---------- register sibling routers ----------
# Разделы, чьи маршруты до деления core.py висели прямо на app, — первыми и в прежнем порядке
app.include_router(health.router)
app.include_router(source_api.router)
app.include_router(random_api.router)
app.include_router(catalog.router)
app.include_router(auth.router)
app.include_router(social_login.router)
app.include_router(account.router)
app.include_router(friends.router)
app.include_router(messages.router)
app.include_router(realtime_ws.router)
app.include_router(lists.router)
app.include_router(activity.router)
app.include_router(profile.router)
app.include_router(profile_pages.router)
app.include_router(player_proxy.router)
app.include_router(player_proxy.kodik_router)
app.include_router(title_pages.router)
app.include_router(seo.router)
app.include_router(video_reports.router)
app.include_router(admin_router.router)
app.add_exception_handler(admin_auth.AdminError, admin_auth.error_response)


# Панели Anime Social ошибки — в обёртке API v2 и по-русски, остальным — как обычно у FastAPI
@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """Ошибка тела или параметров: админ-API — в обёртке API v2 и по-русски, остальным — как у FastAPI."""
    if request.url.path.startswith("/api/admin/"):
        field = ".".join(str(part) for part in (exc.errors() or [{}])[0].get("loc", ()) if part != "body")
        message = f"Неверное поле: {field}" if field else "Неверное тело запроса"
        return admin_auth.error_response(request, admin_auth.AdminError(422, "validation_failed", message))
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    """HTTP-ошибка: админ-API — в обёртке API v2 и по-русски, остальным — как у FastAPI."""
    if request.url.path.startswith("/api/admin/"):
        code, message = {404: ("not_found", "Нет такого метода"),
                         405: ("method_not_allowed", "Метод не поддерживается")}.get(exc.status_code, ("error", "Ошибка AsoPlay"))
        return admin_auth.error_response(request, admin_auth.AdminError(exc.status_code, code, message))
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    """Непойманное исключение: админ-API — внутренняя ошибка в обёртке, остальным — 500 текстом."""
    # Сам сбой всё равно попадёт в журнал сервера: обработчик только отвечает
    if request.url.path.startswith("/api/admin/"):
        return admin_auth.error_response(request, admin_auth.AdminError(500, "internal_error", "Внутренняя ошибка AsoPlay"))
    return PlainTextResponse("Internal Server Error", status_code=500)


# ---------- static assets + SPA shell ----------
# app.js, /js/*.js, styles.css are served from the project root as-is so the
# dev workflow (open index.html via Live Server) and the production workflow
# (uvicorn serving everything) stay on the same file tree.
class _StaticAssets(StaticFiles):
    """Статика из папки: js, css и картинки — с кешем на сутки."""

    async def get_response(self, path, scope):
        """Ответ StaticFiles; удачный для js, css и картинок — с кешем на сутки."""
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            # Long cache for static assets; index.html (served via title_pages
            # / shell routes) has its own short TTL.
            if path.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")):
                resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp


_ASSET_DIRS = [
    ("/styles.css", env.ROOT / "styles.css"),
    ("/app.js", env.ROOT / "app.js"),
]


def _file_route(app: FastAPI, route: str, path: Path, media: str | None = None) -> None:
    """Отдать файл path по адресу route с кешем на минуту; файла нет — 404."""
    @app.get(route, include_in_schema=False)
    def _serve():
        """Файл с типом по расширению."""
        if not path.is_file():
            raise HTTPException(404)
        data = path.read_bytes()
        ct = media or (
            "text/css" if path.suffix == ".css"
            else "application/javascript" if path.suffix == ".js"
            else "application/octet-stream"
        )
        return Response(
            data, media_type=ct,
            headers={"Cache-Control": "public, max-age=60"},
        )


for _route, _path in _ASSET_DIRS:
    _file_route(app, _route, _path)

# Serve the whole /js/ directory if present (split frontend modules live here).
_JS_DIR = env.ROOT / "js"
if _JS_DIR.is_dir():
    app.mount("/js", _StaticAssets(directory=str(_JS_DIR)), name="js")

# Brand assets: logos, icons. Directory is created on startup so /assets always
# resolves (empty 404 until files are added).
_ASSETS_DIR = env.ROOT / "assets"
_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets", _StaticAssets(directory=str(_ASSETS_DIR)), name="assets")

# Shell routes (home, catalog, trending, account...) go LAST so they don't
# eat API routes like /search or /episodes.
title_pages.register_shell_routes(app)
