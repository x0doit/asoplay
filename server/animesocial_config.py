# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

AnimeSocial link-and-assets config. Reads /animesocial.json once, applies
env overrides (AV_ANIMESOCIAL_SITE_URL wins), and exposes URL builders for
everything that references the partner social network:

    - register / login / recover / profile links
    - avatar URLs (50 / 100 / full sizes)
    - cover and background (fon) URLs
    - no-avatar fallback

The same config is exposed verbatim to the frontend via /auth/config, so
there is exactly ONE place on disk that owns AnimeSocial URLs.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("animeviev.animesocial_config")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _PROJECT_ROOT / "animesocial.json"


def _load_raw() -> dict[str, Any]:
    if not _CONFIG_FILE.is_file():
        log.warning("animesocial.json missing — using hard-coded fallback")
        return {
            "site_name": "AnimeSocial",
            "site_url": "https://animesocial.online",
            "paths": {
                "home": "/", "login": "/login", "register": "/register",
                "recover": "/recover", "profile": "/profile{user_id}",
            },
            "uploads": {
                "avatar_small":  "/uploads/accounts/{user_id}/photo/50_{photo}",
                "avatar_medium": "/uploads/accounts/{user_id}/photo/100_{photo}",
                "avatar_full":   "/uploads/accounts/{user_id}/photo/{photo}",
                "cover":         "/uploads/accounts/{user_id}/cover/{cover}",
                "fon":           "/uploads/accounts/{user_id}/fon/{fon}",
            },
            "fallbacks": {"avatar": "/img/noava.png"},
        }
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("animesocial.json unreadable: %s", exc)
        return {}


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    """Merged config: animesocial.json + env overrides. Cached for the
    process lifetime; restart the server to pick up file edits."""
    raw = _load_raw()

    site_url = os.environ.get("AV_ANIMESOCIAL_SITE_URL")
    if site_url:
        raw["site_url"] = site_url
    raw["site_url"] = raw.get("site_url", "").rstrip("/")

    raw.setdefault("site_name", "AnimeSocial")
    raw.setdefault("paths", {})
    raw.setdefault("uploads", {})
    raw.setdefault("fallbacks", {"avatar": "/img/noava.png"})
    return raw


def site_url() -> str:
    return get_config()["site_url"]


def absolute(path: str) -> str:
    """Join `path` (like '/foo') with site_url. Already-absolute URLs pass through."""
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return site_url() + path


def _resolve_template(template: str, **params: Any) -> str:
    out = template
    for key, value in params.items():
        out = out.replace("{" + key + "}", str(value) if value is not None else "")
    return out


def path_for(key: str, **params: Any) -> str:
    """Absolute URL for one of the named paths (home / login / register / profile / …)."""
    template = get_config()["paths"].get(key, "/")
    return absolute(_resolve_template(template, **params))


def register_url() -> str:
    return path_for("register")


def login_url() -> str:
    return path_for("login")


def recover_url() -> str:
    return path_for("recover")


def profile_url(user_id: int | str) -> str:
    return path_for("profile", user_id=user_id)


def avatar_url(user_id: int | str | None, photo: str | None, size: str = "medium") -> str:
    """Returns an absolute URL for a user's avatar of the requested size
    ('small' → 50px, 'medium' → 100px, 'full' → original). When either
    user_id or photo is missing, returns the no-avatar fallback."""
    cfg = get_config()
    if not user_id or not photo:
        return absolute(cfg["fallbacks"].get("avatar", "/img/noava.png"))
    key_map = {
        "small": "avatar_small",
        "medium": "avatar_medium",
        "full": "avatar_full",
        "large": "avatar_full",
    }
    template = cfg["uploads"].get(key_map.get(size, "avatar_medium"))
    if not template:
        return absolute(cfg["fallbacks"].get("avatar", "/img/noava.png"))
    return absolute(_resolve_template(template, user_id=user_id, photo=photo))


def cover_url(user_id: int | str | None, cover: str | None) -> str:
    if not user_id or not cover:
        return ""
    template = get_config()["uploads"].get("cover")
    if not template:
        return ""
    return absolute(_resolve_template(template, user_id=user_id, cover=cover))


def fon_url(user_id: int | str | None, fon: str | None) -> str:
    if not user_id or not fon:
        return ""
    template = get_config()["uploads"].get("fon")
    if not template:
        return ""
    return absolute(_resolve_template(template, user_id=user_id, fon=fon))


def public_view() -> dict[str, Any]:
    """Safe-to-expose flat dict for /auth/config + frontend bootstrap.
    Contains ready-to-use absolute URLs — the frontend never has to know
    about templates or placeholders."""
    cfg = get_config()
    return {
        "site_name": cfg.get("site_name", "AnimeSocial"),
        "site_url": site_url(),
        "register_url": register_url(),
        "login_url": login_url(),
        "recover_url": recover_url(),
        "profile_url_template": absolute(cfg["paths"].get("profile", "/profile{user_id}")),
        "avatar_url_templates": {
            "small":  absolute(cfg["uploads"].get("avatar_small", "")),
            "medium": absolute(cfg["uploads"].get("avatar_medium", "")),
            "full":   absolute(cfg["uploads"].get("avatar_full", "")),
        },
        "cover_url_template": absolute(cfg["uploads"].get("cover", "")),
        "fon_url_template":   absolute(cfg["uploads"].get("fon", "")),
        "fallbacks": {
            "avatar": absolute(cfg["fallbacks"].get("avatar", "/img/noava.png")),
        },
    }
