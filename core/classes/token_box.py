# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Шифрование токенов Anime Social в базе: AES-256-GCM.

База общая с соцсетью, а соцсеть сама хранит только хеши своих токенов: открытый
токен в aviev_* дал бы любому, кто прочитал базу, доступ к аккаунтам. Ключ —
AV_TOKEN_KEY в .env (32 байта в base64), в базе его нет. Шифротекст привязан к
строке и полю (associated data), поэтому токены нельзя переставить между строками.
Без ключа или пакета cryptography вход через Anime Social выключен.
"""
from __future__ import annotations

import base64
import binascii
import secrets
from typing import Any

from core.config import config

_NONCE_BYTES = 12


def _key() -> bytes | None:
    raw = config.token_key()
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        return None
    return key if len(key) == 32 else None


def _aead() -> Any:
    key = _key()
    if key is None:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return None
    return AESGCM(key)


def available() -> bool:
    return _aead() is not None


def seal(text: str, context: str) -> bytes:
    aead = _aead()
    if aead is None:
        raise RuntimeError("AV_TOKEN_KEY or the cryptography package is missing")
    nonce = secrets.token_bytes(_NONCE_BYTES)
    return nonce + aead.encrypt(nonce, text.encode("utf-8"), context.encode("utf-8"))


def unseal(blob: bytes, context: str) -> str | None:
    """Расшифровать; None — ключ сменился, данные испорчены или из другой строки."""
    aead = _aead()
    if aead is None or not blob or len(blob) <= _NONCE_BYTES:
        return None
    try:
        from cryptography.exceptions import InvalidTag
    except ImportError:
        return None
    try:
        plain = aead.decrypt(bytes(blob[:_NONCE_BYTES]), bytes(blob[_NONCE_BYTES:]), context.encode("utf-8"))
    except InvalidTag:
        return None
    return plain.decode("utf-8")
