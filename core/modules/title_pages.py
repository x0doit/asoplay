"""
Страницы тайтлов по настоящим адресам (/anime/{id}-{slug}/) и маршруты оболочки SPA.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Для вошедшего человека сайт остаётся одностраничным приложением, но у каждого тайтла есть HTML по своему адресу
(core/modules/title_render.py): его читают поисковики и превью ссылок. Данные — из кеша страниц
(core/modules/title_cache.py), его заполняет только сервер из своего каталога: тайтла нет в кеше — загрузка при
заходе, запись устарела — страница из кеша, обновление в фоне. Адрес без slug или с чужим slug ведёт 301 на
канонический; тайтл, скрытый панелью Anime Social, отдаётся с 451 и noindex. Карта сайта и robots.txt —
core/modules/seo.py.

Маршруты оболочки SPA (главная, каталог, личные разделы) core.py подключает последними, после всех API. На шаге 6
рефакторинга они переезжают в core/static.py.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from core.functions import site_settings
from core.modules import title_cache, title_render

router = APIRouter()


# ---------- public routes ----------
@router.get("/anime/{stub}/", response_class=HTMLResponse)
@router.get("/anime/{stub}", response_class=HTMLResponse)
async def canonical_title(stub: str):
    """Страница тайтла /anime/{mal_id}-{slug}/. Нет в кеше — из каталога; каталог недоступен — общая оболочка
    с 503, нет в MAL — заглушка 404; запись устарела — из кеша, обновление в фоне; slug не тот — 301 на
    канонический адрес; скрыт панелью — 451 и noindex."""
    m = re.match(r"^(\d{1,9})(?:-([a-z0-9-]+))?$", stub)
    if not m:
        raise HTTPException(404, "bad anime path")
    mal_id = int(m.group(1))
    incoming_slug = m.group(2) or ""

    info = title_cache.load_cached(mal_id)
    if info is not None and title_cache.stale(info):
        title_cache.refresh_later(mal_id)
    if info is None:
        try:
            info = await title_cache.fetch_and_store(mal_id)
        except HTTPException:
            shell = title_render.render_generic()
            shell.status_code = 503
            shell.headers["Retry-After"] = "30"
            shell.headers["X-Robots-Tag"] = "noindex"
            return shell
        if info is None:
            # ID нет ни в кеше, ни в MAL (например, уже удалён или это невалидный
            # id типа 2). Отдаём HTML-оболочку SPA со статусом 404 + noindex,
            # чтобы:
            #  - пользователь при перезагрузке не получал сырой JSON detail;
            #  - SPA у себя показала человекочитаемый экран с подсказкой;
            #  - краулеры не индексировали несуществующий тайтл.
            return title_render.render_missing_title(mal_id, incoming_slug)
        info = title_cache.load_cached(mal_id) or info

    canonical_slug = info["slug"]
    if incoming_slug != canonical_slug:
        return RedirectResponse(title_cache.canonical_path(mal_id, canonical_slug), status_code=301)

    page = title_render.render_title_page(info)
    if site_settings.hidden(mal_id):
        # Скрыт из панели Anime Social по жалобе правообладателя: страница остаётся (SPA покажет, что видео
        # недоступно), но для поисковиков — 451 и noindex
        page.status_code = 451
        page.headers["X-Robots-Tag"] = "noindex"
        page.headers["Cache-Control"] = "no-cache"
    return page


# ---------- shell routes for the SPA ----------
def register_shell_routes(app) -> None:
    """Attach catch-all HTML routes for SPA-driven paths (home, catalog,
    personal sections). Must be called AFTER all API routers are attached —
    FastAPI dispatches by registration order, so /auth/*, /account/*, /search,
    /episodes… must already be bound to win over these shell responders."""

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def _home():
        """Главная — общая оболочка SPA."""
        return title_render.render_generic()

    for path in ("/top", "/season", "/trending", "/movies",
                 "/catalog", "/filter", "/random", "/login", "/search"):
        _attach_shell(app, path)

    @app.get("/genre/{genre_id}/{name:path}", response_class=HTMLResponse, include_in_schema=False)
    def _genre_shell(genre_id: int, name: str):
        """Жанр каталога — общая оболочка SPA."""
        return title_render.render_generic()

    # /my/favorites, /my/history, /my/watching, /my/settings …
    @app.get("/my/{tail:path}", response_class=HTMLResponse, include_in_schema=False)
    def _personal_shell(tail: str):
        """Личные разделы (/my/…) — общая оболочка SPA."""
        return title_render.render_generic()


def _attach_shell(app, path: str) -> None:
    """Общая оболочка SPA по адресу path."""
    @app.get(path, response_class=HTMLResponse, include_in_schema=False)
    def _shell():
        """Общая оболочка SPA."""
        return title_render.render_generic()
