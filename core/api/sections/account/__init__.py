"""
Данные аккаунта (/account/…) — вход раздела-пакета: подключает части по порядку.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Избранное, «Смотрю», прогресс серий, оценки и любимые озвучки, настройки и приватность, перенос из localStorage.
Всё — только для вошедших (current_user_required): гость получает 401, фронтенд показывает окно входа.
Запись — через core.classes.db.connect(), без ORM: таблицы небольшие и устойчивые, SQL читается и проверяется
глазами. Общие функции прогресса — в core/functions/progress.py и watch_history.py, приватность —
в core/functions/privacy.py.

Части — в порядке подключения, он же порядок маршрутов:
    favorites.py        избранное
    history.py          «Продолжить просмотр»
    progress.py         позиции в сериях, «не смотрел»
    ratings.py          оценки и любимые озвучки
    settings.py         настройки аккаунта и приватность
    import_storage.py   перенос из localStorage
"""
from __future__ import annotations

from fastapi import APIRouter

from core.api.sections.account import favorites, history, import_storage, progress, ratings, settings

router = APIRouter(prefix="/account")
for _part in (favorites, history, progress, ratings, settings, import_storage):  # порядок — как до деления
    router.include_router(_part.router)
