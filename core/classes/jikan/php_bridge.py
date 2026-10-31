"""
Запуск встроенного Jikan: bridge.php в отдельном процессе PHP — проверка, адрес страницы MAL, разбор HTML.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Нужен PHP >= 8.4.4 (AV_JIKAN_PHP или OSPanel) и зависимости Composer в core/classes/jikan/php/vendor. Задание
уходит в процесс JSON-ом через stdin, ответ приходит JSON-ом из stdout; stderr идёт только в журнал и в ответ
наружу не попадает. Каталог (core/classes/jikan/catalog.py) зовёт call через модуль.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from fastapi import HTTPException

from core.config import config

log = logging.getLogger("asoplay.catalog")
_BRIDGE = Path(__file__).with_name("php") / "bridge.php"
_php: str | None = None
_workers = asyncio.Semaphore(4)


def _php_binary() -> str:
    """Путь к PHP: AV_JIKAN_PHP, иначе PHP 8.4 или 8.5 из OSPanel (на Windows), иначе php из PATH. Найденный
    запоминается; нет PHP — 503."""
    global _php
    if _php:
        return _php
    explicit = config.jikan_php()
    candidates = [explicit] if explicit else []
    if not explicit and os.name == "nt":
        root = config.ospanel_dir()
        candidates += [str(root / "modules/PHP-8.4/php.exe"), str(root / "modules/PHP-8.5/php.exe")]
    if not explicit:
        candidates += [shutil.which("php") or ""]
    for candidate in candidates:
        resolved = shutil.which(candidate) if candidate else None
        if resolved:
            _php = resolved
            return _php
    raise HTTPException(503, "Embedded catalog requires PHP >= 8.4.4 (AV_JIKAN_PHP)")


async def call(payload: dict) -> dict:
    """Задание bridge.php в отдельном процессе: JSON в stdin, JSON из stdout, до четырёх процессов сразу. Нет PHP —
    503, сбой парсера — 502 (stderr — только в журнал), дольше 8 секунд — процесс убит и TimeoutError."""
    if not (_BRIDGE.parent / "vendor/autoload.php").is_file():
        raise HTTPException(503, "Install embedded catalog dependencies in core/classes/jikan/php with Composer")
    async with _workers:
        try:
            process = await asyncio.create_subprocess_exec(
                _php_binary(), "-d", "display_errors=stderr", "-d", "memory_limit=128M", str(_BRIDGE),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
        except OSError as exc:
            raise HTTPException(503, "Embedded PHP runtime unavailable") from exc
        try:
            out, err = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), 8)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            # stderr is diagnostic only, never included in a public response.
            log.error("Jikan parser failed: %s", err.decode("utf-8", "replace")[:1000])
            raise HTTPException(502, "Embedded catalog parser failed")
        try:
            return json.loads(out)
        except ValueError as exc:
            raise HTTPException(502, "Invalid embedded parser response") from exc
