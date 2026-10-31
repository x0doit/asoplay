"""
Публичный профиль /@handle: HTML для поисковиков и превью ссылок.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Handle — alias из Just_accounts (например, edward), а если alias пуст — profile{user_id}. Страница — оболочка SPA
(core/modules/title_render.py) с canonical, Open Graph, JSON-LD ProfilePage и noscript; данные профиля фронтенд
берёт из раздела /profile/… (core/api/sections/profile.py).

Приватность:
- если hide_lists → списки не попадают в HTML,
- если hide_activity → блок активности тоже не попадает.
Никакого CSS-скрытия: данные просто не отдаются. Индексируется только «живой» профиль: открыт хотя бы один блок
и у человека есть имя.
"""
from __future__ import annotations

import html
import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from core.classes.accounts import resolve_handle
from core.config.config import SITE_NAME, SITE_URL
from core.functions import lists
from core.functions.privacy import read_privacy
from core.modules import title_render

router = APIRouter()


# ---------- SSR ----------
def _render_shell(head_extra: str, noscript: str, *,
                  title: str | None = None, description: str | None = None,
                  robots: str | None = None) -> HTMLResponse:
    """Оболочка SPA с head_extra и noscript; title, description и robots — в head, кеш — no-cache."""
    shell = title_render.read_shell()
    shell = title_render.override_head(shell, title=title, description=description, robots=robots)
    shell = shell.replace("<!--av:head-extra-->", head_extra, 1)
    shell = shell.replace("<!--av:preload-->", "", 1)
    shell = shell.replace("<!--av:noscript-->", noscript, 1)
    # no-cache: см. title_render.py, та же причина — избегаем застревания
    # пользователя на старой cache-buster версии app.js/styles.css.
    return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})


@router.get("/@{handle}", response_class=HTMLResponse)
@router.get("/@{handle}/", response_class=HTMLResponse)
def profile_page(handle: str):
    """Страница профиля: canonical, Open Graph, JSON-LD ProfilePage и noscript со счётчиками списков, если они
    открыты. Неизвестный handle — оболочка SPA с 404."""
    target = resolve_handle(handle)
    if not target:
        # Неизвестный handle: оболочка SPA с кодом 404, а «Профиль не найден» рисует сам фронтенд.
        # Голый HTTPException отдавал браузеру JSON вместо страницы.
        missing = _render_shell(
            "", "<noscript><h1>Профиль не найден</h1></noscript>",
            title=f"Профиль не найден — {SITE_NAME}", robots="noindex, follow",
        )
        missing.status_code = 404
        return missing

    privacy = read_privacy(target["id"])
    show_lists = not privacy["hide_lists"]
    show_activity = not privacy["hide_activity"]

    canonical = f"{SITE_URL}/@{target['handle']}"
    name = target["name"] or target["handle"]
    description = (
        f"Профиль {name} на {SITE_NAME} — списки аниме и активность пользователя."
        if show_lists or show_activity
        else f"Профиль {name} на {SITE_NAME} — пользователь ограничил публичную видимость."
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "name": f"{name} — {SITE_NAME}",
        "url": canonical,
        "inLanguage": "ru",
        "mainEntity": {
            "@type": "Person",
            "name": name,
            "identifier": target["handle"],
            "image": target.get("avatar") or None,
        },
    }

    # Индексируем только «живые» профили: хотя бы один блок открыт И
    # у пользователя есть подтверждённое имя. Иначе no-index.
    indexable = (show_lists or show_activity) and bool(target.get("name"))
    robots = "index, follow" if indexable else "noindex, follow"

    page_title = f"{name} — {SITE_NAME}"
    head_extra = (
        f'<link rel="canonical" href="{html.escape(canonical)}">\n'
        f'<meta property="og:type" content="profile">\n'
        f'<meta property="og:site_name" content="{html.escape(SITE_NAME)}">\n'
        f'<meta property="og:title" content="{html.escape(name)}">\n'
        f'<meta property="og:description" content="{html.escape(description)}">\n'
        f'<meta property="og:url" content="{html.escape(canonical)}">\n'
        + (f'<meta property="og:image" content="{html.escape(target.get("avatar") or "")}">\n'
           if target.get("avatar") else "")
        + f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>\n'
    )

    noscript_parts = [f'<h1>{html.escape(name)}</h1>']
    if show_lists:
        counts = lists.counts_for(target["id"])
        bits = [f'{k}: {v}' for k, v in counts.items() if v]
        if bits:
            noscript_parts.append(f"<p>{' · '.join(html.escape(b) for b in bits)}</p>")
    else:
        noscript_parts.append("<p>Списки пользователя ограничены настройками приватности.</p>")
    if not show_activity:
        noscript_parts.append("<p>Активность скрыта настройками приватности.</p>")
    noscript_parts.append(f'<p><a href="{SITE_URL}">{html.escape(SITE_NAME)}</a></p>')

    return _render_shell(
        head_extra, "<noscript>" + "".join(noscript_parts) + "</noscript>",
        title=page_title, description=description, robots=robots,
    )
