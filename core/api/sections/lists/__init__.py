"""
Списки тайтлов (/account/lists/…) — вход раздела-пакета: подключает части по порядку.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Всё — только для вошедших (current_user_required). Домен списков и авто-правила — в core/functions/lists.py,
время просмотра — в core/functions/watch_time.py.

Части — в порядке подключения, он же порядок маршрутов (GET /counts стоит раньше GET /{mal_id}, иначе его перехватил
бы /{mal_id}):
    entries.py          записи: все, счётчики, одна запись, статус, избранное, удаление
    progress_event.py   события плеера и ручной прогон правила 30 дней
"""
from __future__ import annotations

from fastapi import APIRouter

from core.api.sections.lists import entries, progress_event

router = APIRouter()
for _part in (entries, progress_event):  # порядок — как до деления
    router.include_router(_part.router, prefix="/account/lists")
