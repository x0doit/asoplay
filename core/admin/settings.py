"""
Админ-API: настройки сайта — плашка, функции, источники видео, хосты картинок через прокси.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/admin: вход router.py подключает части по порядку. Решения хранит и раздаёт сайту
core/functions/site_settings.py; здесь — проверка, сохранение и журнал.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.admin.audit import DETAILS_MAX, audit
from core.admin.auth import AdminError, admin_user, ok
from core.classes import as_sessions
from core.classes.clock import utc_now
from core.classes.db import connect
from core.classes.vpn import bridge as vpn_bridge
from core.functions import image_hosts, player_hosts, site_settings, video_reports

router = APIRouter()


class NoticeIn(BaseModel):
    """Плашка над сайтом: текст и до какого времени её показывать (Unix-секунды, пусто — бессрочно)."""

    text: str = Field(default="", max_length=site_settings.NOTICE_MAX)
    until: int | None = Field(default=None, ge=0)


class FeaturesIn(BaseModel):
    """Выключатели функций сайта; пустое поле — оставить как есть."""

    comments: bool | None = None
    password_login: bool | None = None
    as_login: bool | None = None


class SourceIn(BaseModel):
    """Источник видео: включён ли и его место в порядке перебора плеером."""

    id: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    order: int = Field(default=0, ge=0, le=1000)


class ProxyHostIn(BaseModel):
    """Хост картинок через прокси: имя (сервер приводит его к punycode и нижнему регистру) и путь сервера к нему —
    как обычно (site) или через VPN-мост (vpn)."""
    host: str = Field(min_length=1, max_length=300)
    via: Literal["site", "vpn"] = "site"


class SettingsIn(BaseModel):
    """Настройки сайта целиком; часть, которой нет, не меняется."""

    notice: NoticeIn | None = None
    features: FeaturesIn | None = None
    sources: list[SourceIn] | None = Field(default=None, max_length=50)
    proxy_hosts: list[ProxyHostIn] | None = Field(default=None, max_length=image_hosts.PROXY_HOSTS_MAX)
    players_off: list[str] | None = Field(default=None, max_length=len(player_hosts.PLAYER_KINDS))


def _settings_view() -> dict[str, Any]:
    """Настройки для панели — и что из выключенного вообще можно включить."""
    current = site_settings.state.settings
    return {
        "notice": dict(current.notice),
        "features": dict(current.features),
        "sources": site_settings.sources_view(),
        "proxy_hosts": [dict(item) for item in current.proxy_hosts],
        "players_off": list(current.players_off),
        # Что можно включить: вход через Anime Social — только при ключах клиента в .env AsoPlay,
        # комментариев на AsoPlay пока нет
        # vpn — работает ли VPN-мост на сервере сейчас (via "vpn" без него идёт напрямую)
        "available": {"as_login": as_sessions.enabled(), "password_login": True, "comments": False,
                      "vpn": vpn_bridge.is_active(), "players": list(player_hosts.PLAYER_KINDS)},
    }


@router.get("/settings")
def settings_get(_admin: int = Depends(admin_user)) -> dict[str, Any]:
    """GET /api/admin/settings — настройки сайта."""
    return ok(_settings_view())


def _sources_key(sources: Any) -> list[tuple[str, bool, int]]:
    """Источники в сравнимом виде: id, включён ли, место в порядке."""
    return sorted((s["id"], bool(s.get("enabled", True)), int(s.get("order", 0))) for s in sources)


def _proxy_hosts_diff(old: Any, new: list[dict[str, str]]) -> dict[str, Any]:
    """Запись журнала о списке хостов: added, removed, changed (сменился via), пустые части тоже есть. Сам список
    лежит в настройках. Что не влезло в запись журнала, отрезается с конца самой длинной части; omitted — сколько."""
    before = {item["host"]: item["via"] for item in old}
    after = {item["host"]: item["via"] for item in new}
    details: dict[str, Any] = {
        "added": [{"host": h, "via": v} for h, v in after.items() if h not in before],
        "removed": [{"host": h, "via": v} for h, v in before.items() if h not in after],
        "changed": [{"host": h, "via": v, "was": before[h]} for h, v in after.items()
                    if h in before and before[h] != v],
        "omitted": 0,
    }
    while len(json.dumps(details, ensure_ascii=False)) > DETAILS_MAX:
        details[max(("added", "removed", "changed"), key=lambda part: len(details[part]))].pop()
        details["omitted"] += 1
    return details


@router.put("/settings")
def settings_put(body: SettingsIn, admin: int = Depends(admin_user)) -> dict[str, Any]:
    """Панель присылает настройки целиком; сохраняется, объявляется вкладкам и пишется в журнал только то,
    что изменилось."""
    current = site_settings.state.settings
    changes: dict[str, Any] = {}
    if body.notice is not None:
        notice = {"text": video_reports.clean(body.notice.text, site_settings.NOTICE_MAX), "until": body.notice.until or None}
        if notice != {"text": current.notice.get("text") or "", "until": current.notice.get("until")}:
            changes["notice"] = notice
    if body.features is not None:
        features = dict(current.features) | {k: v for k, v in body.features.model_dump().items() if v is not None}
        if not features["password_login"] and not features["as_login"]:
            raise AdminError(422, "no_sign_in_left", "Нельзя выключить и вход по почте, и вход через Anime Social")
        if features != dict(current.features):
            changes["features"] = features
    if body.sources is not None:
        known = {s["id"] for s in site_settings.sources_view()}
        unknown = sorted({s.id for s in body.sources} - known)
        if unknown:
            raise AdminError(422, "unknown_source", "Нет таких источников: " + ", ".join(unknown))
        sources = [s.model_dump() for s in body.sources]
        if _sources_key(sources) != _sources_key(site_settings.sources_view()):
            changes["sources"] = sources
    if body.proxy_hosts is not None:
        try:
            proxy_hosts = image_hosts.parse_proxy_hosts([h.model_dump() for h in body.proxy_hosts], strict=True)
        except ValueError as exc:
            why = f" — {exc.args[1]}" if len(exc.args) > 1 else ""  # общий суффикс: почему не годится
            raise AdminError(422, "invalid_host", f"Неверный хост: {exc.args[0]}{why}") from exc
        if proxy_hosts != list(current.proxy_hosts):
            changes["proxy_hosts"] = proxy_hosts
    if body.players_off is not None:
        unknown = sorted(set(body.players_off) - set(player_hosts.PLAYER_KINDS))
        if unknown:
            raise AdminError(422, "unknown_player", "Нет таких плееров: " + ", ".join(unknown))
        players_off = list(dict.fromkeys(body.players_off))
        if players_off != list(current.players_off):
            changes["players_off"] = players_off
    if not changes:
        return ok(_settings_view())
    now = utc_now()
    with connect() as conn:
        cur = conn.cursor()
        for name, value in changes.items():
            cur.execute(
                """INSERT INTO aviev_site_settings (name, value, updated_by, updated_at) VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE value = VALUES(value), updated_by = VALUES(updated_by), updated_at = VALUES(updated_at)""",
                (name, json.dumps(value, ensure_ascii=False), admin, now),
            )
        # Как показывает панель: settings.notice | settings.features | settings.sources | settings.proxy_hosts |
        # settings.players (выключенные виды плееров: added — выключены, removed — включены)
        for name, value in changes.items():
            if name == "players_off":
                details = {"added": [k for k in value if k not in current.players_off],
                           "removed": [k for k in current.players_off if k not in value]}
                audit(cur, admin, "settings.players", "settings:players", details)
                continue
            details = _proxy_hosts_diff(current.proxy_hosts, value) if name == "proxy_hosts" else {name: value}
            audit(cur, admin, f"settings.{name}", f"settings:{name}", details)
        conn.commit()
    site_settings.state.load()
    site_settings.changed(set(changes))
    return ok(_settings_view())
