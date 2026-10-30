"""
Админ-API для раздела «AsoPlay» в /cpanel Anime Social (задача движку, пункт 10) — вход: подключает части по порядку.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Панель — единственный клиент: она ходит на http://127.0.0.1:8787/api/admin/… с этой же машины и подписывает
каждый запрос общим секретом AV_ADMIN_SECRET (он же — в настройках раздела на стороне движка):

    X-AsoPlay-Admin-User: id администратора Anime Social — для журнала действий
    X-AsoPlay-Admin: "{ts}.{hex}",
        hex = HMAC-SHA256(secret, ts + "\\n" + METHOD + "\\n" + path_with_query + "\\n" + admin_user + "\\n" + body)

path_with_query — путь с запросом ровно так, как он ушёл (уже закодированный), body — сырое тело ("" у GET).
id администратора входит в подпись (в задаче движку его не было): иначе кто угодно на пути запроса мог бы записать
действие в журнал на другого. Подпись старше MAX_AGE секунд отклоняется, повтор изменяющего запроса — тоже.
Через публичный прокси (есть X-Forwarded-For или X-Real-IP) API не отвечает вовсе. Ответы — как в API v2:
{ok: true, data} или {ok: false, error: {code, message}}. Кто из администраторов что видит (admin — всё,
chief_moderator и moderator — жалобы и тайтлы), решает панель.

Части — в порядке подключения, он же порядок маршрутов:
    auth.py      подпись и повторы, ошибки и обёртка ответов, страницы списков (основа частей, без маршрутов)
    reports.py   жалобы «Не работает»
    audit.py     журнал действий
    settings.py  настройки сайта
    titles.py    тайтлы: поиск, пометки, сброс кеша
    summary.py   обзор
"""
from __future__ import annotations

from fastapi import APIRouter

from core.admin import audit, reports, settings, summary, titles

router = APIRouter(prefix="/api/admin")
for _part in (reports, audit, settings, titles, summary):  # порядок — как до деления: он решает, какой маршрут ответит
    router.include_router(_part.router)
