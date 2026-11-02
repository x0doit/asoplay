"""
Кеш картинок /proxy/img на диске: файл на картинку, срок жизни, предел размера с уборкой сразу при переполнении.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Своя папка config.IMG_CACHE_DIR, файл на картинку: строка JSON (тип, когда взята) и тело; запись — заменой файла
целиком. Ключ — приведённый адрес (core/classes/image_proxy.py canonical): варианты одного адреса ложатся в один
файл. Свежая картинка — моложе TTL; старая годится, пока идёт обновление и когда хост недоступен.

Размер кеша считается: первая запись после запуска обходит папку, дальше каждая запись прибавляет свои байты.
Вышли за MAX_CACHE_BYTES — уборка сразу: самые давно нужные файлы удаляются, пока кеш не станет меньше LOW_WATER
от предела. Время файла — время выборки; картинка из кеша освежает его раз в сутки. Функции здесь блокирующие:
image_proxy зовёт их через asyncio.to_thread.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

from core.config import config

TTL = 86400 * 7
MAX_CACHE_BYTES = 1024 ** 3
LOW_WATER = 0.9
_TOUCH_AFTER = 86400.0

_size: int | None = None  # байт в кеше; None — ещё не считали с запуска
_cleaning = False
_lock = threading.Lock()


def path(key: str) -> Path:
    """Файл картинки в кеше."""
    return config.IMG_CACHE_DIR / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".img")


def lookup(key: str) -> tuple[bytes, str, bool] | None:
    """Картинка из кеша: (тело, тип, свежая ли — моложе TTL); нет или испорчена — None."""
    file = path(key)
    try:
        head, _, body = file.read_bytes().partition(b"\n")
        info = json.loads(head)
        age, content_type = time.time() - float(info["at"]), str(info["type"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    with contextlib.suppress(OSError):  # не вышло освежить время файла — картинка всё равно есть
        if time.time() - file.stat().st_mtime > _TOUCH_AFTER:
            os.utime(file)
    return body, content_type, age <= TTL


def store(key: str, content: bytes, content_type: str) -> None:
    """Положить картинку в кеш заменой файла целиком и учесть её байты; переполнение — уборка сразу."""
    file = path(key)
    tmp = file.with_name(f"{file.name}.{secrets.token_hex(4)}.tmp")
    data = json.dumps({"type": content_type, "at": time.time()}).encode("utf-8") + b"\n" + content
    try:
        config.IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            old = file.stat().st_size
        except OSError:
            old = 0
        tmp.write_bytes(data)
        os.replace(tmp, file)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return
    _grow(len(data) - old)


def _grow(delta: int) -> None:
    """Прибавить запись к размеру кеша; размер ещё не известен или вышел за предел — обход папки с уборкой."""
    global _size, _cleaning
    with _lock:
        if _size is not None:
            _size += delta
            if _size <= MAX_CACHE_BYTES:
                return
        if _cleaning:
            return
        _cleaning = True
    total = None  # обход упал — размер снова неизвестен, следующая запись обойдёт папку
    try:
        total = cleanup()  # None — папку не обошли: размер остаётся неизвестным
    finally:
        with _lock:
            _size, _cleaning = total, False


def cleanup(limit: int | None = None, target: int | None = None) -> int | None:
    """Обойти папку; кеш больше limit — удалять самые давно нужные файлы, пока он больше target. Ответ — байт
    в кеше после уборки; папку обойти не вышло — None (размер неизвестен, а не ноль)."""
    limit = MAX_CACHE_BYTES if limit is None else limit
    target = int(limit * LOW_WATER) if target is None else target
    files: list[tuple[float, int, str]] = []
    try:
        with os.scandir(config.IMG_CACHE_DIR) as entries:
            for entry in entries:
                with contextlib.suppress(OSError):
                    if entry.is_file():
                        info = entry.stat()
                        files.append((info.st_mtime, info.st_size, entry.path))
    except FileNotFoundError:
        return 0  # папки ещё нет — кеш пуст
    except OSError:
        return None
    total = sum(size for _, size, _ in files)
    if total <= limit:
        return total
    for _, size, name in sorted(files):
        if total <= target:
            break
        try:
            os.unlink(name)
        except OSError:
            continue
        total -= size
    return total


def forget_size() -> None:
    """Забыть посчитанный размер: следующая запись обойдёт папку заново (проверки и смена папки)."""
    global _size
    with _lock:
        _size = None
