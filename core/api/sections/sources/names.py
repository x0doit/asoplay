"""
Раздел /src/…: имена озвучек для выбора любимой в настройках.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/sources: вход (__init__.py) подключает части по порядку. Реестр имён ведёт
core/functions/dub_names.py; здесь — открытый ответ со списком, его можно держать в кеше 10 минут.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.functions import dub_names

router = APIRouter()


@router.get("/src/dub-names")
async def dub_names_list() -> JSONResponse:
    """Озвучки, которые встречались в плеере: частые первыми, плюс частые имена, которых ещё нет."""
    names = await asyncio.to_thread(dub_names.listing)
    return JSONResponse(names, headers={"Cache-Control": "public, max-age=600"})
