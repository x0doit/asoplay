"""
Поиск и просмотр у источников (/src/…) — вход раздела-пакета: подключает части по порядку.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Фронтенд ищет тайтл у источника, затем по ключам — его серии, озвучки серии и видео. Ключи и вызов источника —
core/functions/sources.py, свои источники — core/functions/native_sources.py, фильтры —
core/functions/source_filters.py.

Части — в порядке подключения, он же порядок маршрутов:
    search.py   /src/search, /src/episodes
    media.py    /src/dubs, /src/videos
"""
from __future__ import annotations

from fastapi import APIRouter

from core.api.sections.sources import media, search

router = APIRouter()
for _part in (search, media):  # порядок — как до деления
    router.include_router(_part.router)
