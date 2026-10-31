"""
Проверка пароля по хешу из таблицы аккаунтов AnimeSocial: bcrypt, argon2, sha256, sha1, md5, md5 с солью, DLE.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Способ задаёт AV_AUTH_VERIFIER; в режиме auto он выбирается по виду хеша. Хеши сравниваются за постоянное время
(hmac.compare_digest); bcrypt и argon2 — необязательные пакеты, без них такой хеш не подходит. Зовут через модуль.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re

from core.config import config

log = logging.getLogger("asoplay.auth")


# ---------- password verification ----------
def verify_password(plain: str, stored_hash: str, salt: str | None) -> bool:
    """Try the configured verifier first; in 'auto' mode, dispatch by hash shape."""
    stored = (stored_hash or "").strip()
    if not stored:
        return False

    strategies: list[str]
    if config.AUTH_VERIFIER == "auto":
        if stored.startswith(("$2a$", "$2b$", "$2y$")):
            strategies = ["bcrypt"]
        elif stored.startswith("$argon2"):
            strategies = ["argon2"]
        elif re.fullmatch(r"[0-9a-fA-F]{64}", stored):
            strategies = ["sha256"]
        elif re.fullmatch(r"[0-9a-fA-F]{40}", stored):
            strategies = ["sha1"]
        elif re.fullmatch(r"[0-9a-fA-F]{32}", stored):
            strategies = ["dle", "md5_salt", "md5"]
        else:
            strategies = ["plain"]
    else:
        strategies = [config.AUTH_VERIFIER]

    for how in strategies:
        try:
            if _check_one(how, plain, stored, salt):
                return True
        except Exception as exc:
            log.debug("verifier %s raised: %s", how, exc)
    return False


def _check_one(how: str, plain: str, stored: str, salt: str | None) -> bool:
    """Один способ проверки: совпал ли пароль с хешем; пакета нет или способ не подходит — False."""
    if how == "bcrypt":
        try:
            import bcrypt  # type: ignore
        except ImportError:
            return False
        return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("utf-8"))

    if how == "argon2":
        try:
            from argon2 import PasswordHasher  # type: ignore
            from argon2.exceptions import VerifyMismatchError  # type: ignore
        except ImportError:
            return False
        try:
            PasswordHasher().verify(stored, plain)
            return True
        except VerifyMismatchError:
            return False

    if how == "sha256":
        return hmac.compare_digest(hashlib.sha256(plain.encode("utf-8")).hexdigest(), stored.lower())

    if how == "sha1":
        return hmac.compare_digest(hashlib.sha1(plain.encode("utf-8")).hexdigest(), stored.lower())

    if how == "md5":
        return hmac.compare_digest(hashlib.md5(plain.encode("utf-8")).hexdigest(), stored.lower())

    if how == "md5_salt" and salt:
        digest = hashlib.md5((plain + salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, stored.lower())

    if how == "dle" and salt:
        # DataLife Engine style: md5(md5(password) + salt)
        inner = hashlib.md5(plain.encode("utf-8")).hexdigest()
        outer = hashlib.md5((inner + salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(outer, stored.lower())

    if how == "plain":
        return hmac.compare_digest(plain, stored)

    return False
