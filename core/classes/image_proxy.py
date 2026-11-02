"""
Картинки для /proxy/img: кеш, одна выборка на одинаковые адреса, старая копия, пределы выборок, маршруты для панели.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Браузер берёт картинку через сайт, когда сам до хоста не достаёт (js/images.js). Адрес приводится (canonical):
схема, хост в нижнем регистре без точки в конце, порт — только не обычный, путь и запрос; без фрагмента и логина.
По нему идут и запрос наверх, и кеш (core/classes/image_cache.py), так что варианты одного адреса — одна картинка.

Свежая картинка — из кеша. Нет — одна выборка на все одинаковые адреса; есть только старая — она отдаётся сразу,
а выборка обновляет её в фоне. Выборка наверх: сначала напрямую, через VPN-мост — если напрямую не вышло (сеть или
непубличный адрес от своего DNS), via "vpn" — мост первым (core/classes/image_fetch.py). Ответ хоста, даже 5xx
или 429, второго прохода не получает; сетевой сбой обоих путей — ещё один проход через _RETRY_PAUSE.

Пределы: новая выборка наверх спрашивает take у вызывающего (предел промахов на посетителя; нельзя — Busy 429
или старая копия). Места выборок — всего, на посетителя и на хост (core/classes/image_limits.py): ждать места дольше
_SLOT_WAIT — Busy 503. Хост, который подряд не отвечает по сети, отдыхает: к нему не ходим — старая копия или 502.
По каждой строке списка хостов routes помнит последний запрос наверх: каким путём, код и ok — хост ответил (сбой —
сеть самого хоста строки, 5xx, 429 и отказ по адресу).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import httpx

from core.classes import http, image_cache, image_fetch, image_limits
from core.classes.vpn import bridge as vpn_bridge

_DEADLINE = 30.0
_RETRY_PAUSE = 0.7
_SLOT_WAIT = 10.0
_ALLOWED = frozenset(image_fetch.TYPES.values())

_pending: dict[str, asyncio.Task] = {}
slots = image_limits.Slots()
breaker = image_limits.Breaker()
routes: dict[str, dict[str, Any]] = {}  # строка списка хостов → {route, ok, status, at}

Refused = image_fetch.Refused
Failed = image_fetch.Failed


class Busy(Exception):
    """Сейчас не выбираем: посетитель исчерпал промахи (429) или все места выборок заняты (503)."""

    def __init__(self, status: int, retry_after: int):
        """status — код ответа сайта, retry_after — через сколько секунд повторить."""
        super().__init__(f"busy: {status}")
        self.status, self.retry_after = status, retry_after


def canonical(url: str) -> str:
    """Адрес для запроса наверх и ключа кеша: схема, хост без точки в конце, порт — только не обычный, путь и запрос.
    Адрес уже проверен url_refusal (core/functions/image_hosts.py)."""
    parsed = httpx.URL(url)
    host = parsed.raw_host.decode("ascii").lower().rstrip(".")
    port = "" if parsed.port in (None, {"http": 80, "https": 443}[parsed.scheme]) else f":{parsed.port}"
    return f"{parsed.scheme}://{host}{port}{parsed.raw_path.decode('ascii')}"


def _note(key: str, route: str, status: int | None, ok: bool) -> None:
    """Запомнить последний запрос наверх для строки списка хостов (для /summary)."""
    if key:
        routes[key] = {"route": route, "ok": ok, "status": status, "at": int(time.time())}


async def _one_pass(url: str, allowed: Callable[[httpx.URL], bool], via: str, key: str) -> tuple[bytes, str]:
    """Один проход по путям. Дальше по путям — только при сетевом сбое или непубличном адресе от своего DNS: ответ
    хоста, даже ошибка, — уже ответ. Непубличный адрес по DoH через мост — отказ, даже если другой путь упал по сети.

    Строке /summary пишется ответ хоста (ok — он ответил, сбой — 5xx и 429) и отказ. Сетевой сбой — только когда это
    сам хост строки: поддомен, которого нет (NXDOMAIN), строку «сбоем» не делает.
    """
    host = httpx.URL(url).raw_host.decode("ascii")
    own = host == key
    order = ["vpn", "direct"] if via == "vpn" else http.route_order(host)
    paths = [route for route in order if route == "direct" or vpn_bridge.proxy_url() is not None]
    failure: image_fetch.Failed | None = None
    refused: dict[str, image_fetch.AddressRefused] = {}
    for n, route in enumerate(paths):
        timeout = image_fetch.FIRST_OF_TWO if n == 0 and len(paths) > 1 else image_fetch.TIMEOUT
        try:
            content, content_type = await image_fetch.get(route, url, allowed, timeout)
        except image_fetch.AddressRefused as exc:
            if own:
                _note(key, route, 403, False)
            refused[route] = exc
            continue
        except image_fetch.Refused as exc:
            _note(key, route, exc.upstream or 403, exc.upstream is not None)
            raise
        except image_fetch.Failed as exc:
            if exc.upstream is not None or own:
                _note(key, route, exc.upstream, exc.upstream is not None and exc.upstream < 500
                      and exc.upstream != 429)
            if not exc.network:
                raise
            failure = exc
            continue
        except asyncio.CancelledError:  # срок выборки вышел посреди этого пути
            if own:
                _note(key, route, None, False)
            raise
        _note(key, route, 200, True)
        if via != "vpn":
            http.remember_route(host, route)
        return content, content_type
    for route in ("vpn", None if failure else "direct"):
        if route in refused:
            raise refused[route]
    if failure is not None:
        raise failure
    raise image_fetch.Failed(502, "no route", network=True)


async def _attempts(url: str, allowed: Callable[[httpx.URL], bool], via: str, key: str) -> tuple[bytes, str]:
    """Проход по путям; сетевой сбой — пауза и ещё один проход. Ответ хоста повтора не получает."""
    try:
        return await _one_pass(url, allowed, via, key)
    except image_fetch.Failed as exc:
        if not exc.network:
            raise
    await asyncio.sleep(_RETRY_PAUSE)
    return await _one_pass(url, allowed, via, key)


async def _fetch_once(url: str, allowed: Callable[[httpx.URL], bool], via: str, key: str,
                      visitor: str) -> tuple[bytes, str]:
    """Выборка наверх в пределах своих мест и срока; удалась — в кеш. Хост на паузе — сразу 502, без попыток."""
    host = httpx.URL(url).raw_host.decode("ascii")
    resting = breaker.resting(host)
    if resting:
        raise image_fetch.Failed(502, f"host resting {resting:.0f}s", network=True)
    try:
        await slots.acquire(visitor, host, _SLOT_WAIT)
    except TimeoutError:
        raise Busy(503, 5) from None
    try:
        content, content_type = await asyncio.wait_for(_attempts(url, allowed, via, key), _DEADLINE)
    except asyncio.TimeoutError:
        breaker.failed(host)
        raise image_fetch.Failed(504, "timed out", network=True) from None
    except image_fetch.Failed as exc:
        (breaker.failed if exc.network else breaker.answered)(host)
        raise
    finally:
        slots.release(visitor, host)
    breaker.answered(host)
    await asyncio.to_thread(image_cache.store, url, content, content_type)
    return content, content_type


def _start(url: str, allowed: Callable[[httpx.URL], bool], via: str, key: str, visitor: str) -> asyncio.Task:
    """Новая выборка; одна на все одинаковые адреса, пока не кончится. Место занимает её начавший посетитель."""
    task = asyncio.create_task(_fetch_once(url, allowed, via, key, visitor))
    _pending[url] = task

    def finished(done: asyncio.Task) -> None:
        """Убрать выборку из ожидающих и забрать её исключение, чтобы asyncio не писал его в журнал."""
        _pending.pop(url, None)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    return task


async def fetch(url: str, *, allowed: Callable[[httpx.URL], bool], via: str = "site", key: str = "",
                take: Callable[[], int] = lambda: 0, visitor: str = "") -> tuple[bytes, str]:
    """Картинка по адресу: (тело, тип).

    allowed — можно ли идти по адресу (проверяется на каждом шаге); via "vpn" — мост первым; key — строка списка
    хостов для routes; take — можно ли начать новую выборку наверх: 0 — да (и засчитать), иначе — через сколько
    секунд; visitor — ключ посетителя для его доли мест. Refused (403), Failed (status), Busy (429 или 503).
    """
    url = canonical(url)
    found = await asyncio.to_thread(image_cache.lookup, url)
    old = found[:2] if found is not None and found[1] in _ALLOWED else None
    if old is not None and found[2]:
        return old
    task = _pending.get(url)
    if task is None:
        wait = take()
        if wait:
            if old is not None:
                return old
            raise Busy(429, wait)
        task = _start(url, allowed, via, key, visitor)
    if old is not None:
        return old  # старая копия сразу, выборка обновляет её в фоне
    return await asyncio.shield(task)


async def shutdown() -> None:
    """Отменить выборки, которые ещё идут, и закрыть клиенты путей."""
    pending = list(_pending.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _pending.clear()
    await image_fetch.shutdown()
