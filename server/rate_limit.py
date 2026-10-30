# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Ограничение частоты попыток (скользящее окно) и настоящий IP посетителя за
прокси aaPanel.

AsoPlay работает одним процессом, поэтому счётчики живут в памяти: после
перезапуска они обнуляются, и это допустимо. Число ключей ограничено, чтобы
перебор с множества адресов не съел память.
"""
from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque
from typing import Callable, Deque

from fastapi import Request

_LOOPBACK = {"127.0.0.1", "::1"}


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def from_local_proxy(request: Request) -> bool:
    """Запрос пришёл с этого же сервера — через nginx aaPanel, который стоит перед
    AsoPlay. Только у таких запросов заголовкам X-Real-IP / X-Forwarded-* можно верить."""
    return bool(request.client) and request.client.host in _LOOPBACK


def client_ip(request: Request) -> str:
    """IP посетителя. nginx сам выставляет X-Real-IP, а в X-Forwarded-For последний
    адрес добавлен им; всё остальное мог подставить клиент."""
    peer = request.client.host if request.client else ""
    if from_local_proxy(request):
        real = (request.headers.get("x-real-ip") or "").strip()
        if _valid_ip(real):
            return real
        chain = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        if chain and _valid_ip(chain[-1]):
            return chain[-1]
    return peer


class SlidingWindow:
    """Не больше `limit` событий за `window` секунд на один ключ."""

    def __init__(self, limit: int, window: float, max_keys: int = 50_000,
                 clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> Deque[float] | None:
        hits = self._hits.get(key)
        if hits is None:
            return None
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if not hits:
            del self._hits[key]
            return None
        return hits

    def retry_after(self, key: str) -> int:
        """Сколько секунд ждать до следующей попытки; 0 — можно сейчас."""
        now = self._clock()
        with self._lock:
            hits = self._trim(key, now)
            if hits is None or len(hits) < self.limit:
                return 0
            return max(1, int(hits[0] + self.window - now + 0.999))

    def hit(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            hits = self._trim(key, now)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    self._evict(now)
                hits = self._hits[key] = deque()
            hits.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _evict(self, now: float) -> None:
        # Сначала истёкшие ключи, затем самые давние: словарь хранит порядок добавления
        for key in list(self._hits):
            self._trim(key, now)
        while len(self._hits) >= self.max_keys:
            del self._hits[next(iter(self._hits))]
