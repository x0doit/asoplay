"""
Состояние сервера: /health, /sources, /source-health и фильтр рекламы /adblock/….

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

/health — то, что вкладкам нужно знать о сервере: источники для плеера, VPN, каталог, база, фильтр рекламы,
здоровье источников и решения панели Anime Social. Вкладкам с живым соединением то же приходит по /ws: его даёт
realtime_social.status_provider, который связывает core.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from core.classes import db
from core.classes.adblock import engine as adblock
from core.classes.jikan import catalog as jikan_catalog
from core.classes.vpn import bridge as vpn_bridge
from core.functions import site_settings, source_health
from core.functions import sources as source_registry  # маршрут sources перепривязал бы имя модуля

router = APIRouter()


@router.get("/health")
def health():
    """Состояние сервера для вкладок: источники для плеера, VPN, каталог, база, фильтр рекламы,
    здоровье источников и решения панели."""
    active_sources = source_registry.player_sources()
    return {
        "ok": True,
        "sources": active_sources,
        "vpn": vpn_bridge.is_active(),
        "catalog": jikan_catalog.status(),
        "db": db.health(),
        "adblock": adblock.status(),
        "source_health": source_health.snapshot(source_registry.all_sources()),
        "site": site_settings.public_state(),
    }


@router.get("/sources")
def sources():
    """Источники, которые сейчас не остывают."""
    return source_health.available_sources(source_registry.all_sources())


@router.get("/source-health")
def source_health_status():
    """Здоровье каждого источника: доступен ли, сколько ждать, успехи и сбои."""
    return source_health.snapshot(source_registry.all_sources())


@router.get("/adblock/status")
def adblock_status():
    """Состояние фильтра рекламы: сколько правил, когда обновлены."""
    return adblock.status()


@router.get("/adblock/check")
def adblock_check(url: str = Query(..., min_length=4)):
    """Заблокировал бы фильтр рекламы этот адрес и по какому правилу."""
    blocked, reason = adblock.should_block(url)
    return {"blocked": blocked, "reason": reason}
