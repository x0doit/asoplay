"""
VPN-мост: запасной путь для запросов к хосту, недоступному напрямую, — жизненный цикл локального xray.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Запросы идут напрямую; только хост, недоступный напрямую, повторяется через локальный HTTP-прокси моста
(core/classes/http.py, route_order и _source_call в core/core.py). Конфиг и процесс xray — в core/classes/vpn/xray.py;
его функции и константы берутся через модуль (xray.launch и т. д.), чтобы у каждой была одна точка подмены. При
импорте ничего не запускается: activate() зовёт обработчик старта FastAPI.
"""
from __future__ import annotations

import atexit
import logging
import os
import subprocess
import threading

from core.classes.vpn import xray
from core.config import config

log = logging.getLogger("asoplay.vpn")

VPN_PROXY_URL = xray.VPN_PROXY_URL  # для core.py (журнал старта) и proxy_url()
_BRIDGE_PROXY_VALUES = {xray.VPN_PROXY_URL, xray.VPN_SOCKS_URL}

_xray_proc: subprocess.Popen | None = None
_bridge_desired = False
_restart_lock = threading.Lock()
_watchdog_stop = threading.Event()
_watchdog_thread: threading.Thread | None = None


def is_enabled() -> bool:
    """Мост включён настройкой (AV_VPN_ENABLED)."""
    return config.vpn_enabled()


def _restart_bridge_locked() -> bool:
    """Поднять мост заново, если порт не отвечает (под _restart_lock)."""
    if not is_enabled():
        return False
    if xray.port_alive(xray.VPN_HOST, xray.VPN_HTTP_PORT, timeout=0.25):
        return True
    log.warning("VPN bridge port %s is down; restarting bridge", VPN_PROXY_URL)
    return activate()


def _restart_bridge_async() -> None:
    """Перезапуск моста в фоне, если он ещё не идёт."""
    if _restart_lock.locked():
        return

    def _run() -> None:
        """Перезапуск под замком."""
        with _restart_lock:
            _restart_bridge_locked()

    threading.Thread(
        target=_run,
        name="asoplay-vpn-restart",
        daemon=True,
    ).start()



def activate() -> bool:
    """Spin up xray. Returns True when its HTTP proxy is up; callers send a
    request through it only after the direct path to that host failed."""
    global _xray_proc, _bridge_desired
    clear_env()
    if not is_enabled():
        _bridge_desired = False
        log.info("VPN bridge disabled by AV_VPN_ENABLED; using direct outbound")
        return False
    if xray.port_alive(xray.VPN_HOST, xray.VPN_HTTP_PORT):
        _bridge_desired = True
        log.info("VPN already listening on %s (reusing)", VPN_PROXY_URL)
        return True
    config_path = xray.prepare_runtime()
    if config_path is None:
        _bridge_desired = False
        return False
    _bridge_desired = True
    _xray_proc = xray.launch(config_path)
    if _xray_proc is None:
        return False
    atexit.register(shutdown)
    return True


def clear_env() -> None:
    """Remove proxy variables that point at this bridge.

    The bridge is a per-request fallback, never a process-wide proxy, so such
    variables can only come from an older version or a parent process. Custom
    user proxy variables are left untouched.
    """
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        value = (os.environ.get(key) or "").rstrip("/")
        if value in _BRIDGE_PROXY_VALUES:
            os.environ.pop(key, None)


def ensure_active(*, blocking: bool = True) -> bool:
    """Keep the local xray port up while the bridge is wanted.

    A dead port is restarted once for the current caller; without blocking the
    restart runs in the background and the caller stays on the direct path.
    """
    global _bridge_desired
    if not is_enabled():
        _bridge_desired = False
        return False
    if xray.port_alive(xray.VPN_HOST, xray.VPN_HTTP_PORT, timeout=0.25):
        return True
    if not _bridge_desired:
        return False
    if not blocking:
        _restart_bridge_async()
        return False
    with _restart_lock:
        return _restart_bridge_locked()


def start_watchdog() -> None:
    """Start a lightweight supervisor for the local xray bridge."""
    global _watchdog_thread
    if not is_enabled():
        return
    if _watchdog_thread and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()
    interval = config.vpn_watchdog_interval()

    def _loop() -> None:
        """Раз в interval секунд: мост нужен, а порт молчит — поднять."""
        while not _watchdog_stop.wait(interval):
            if not _bridge_desired:
                continue
            if xray.port_alive(xray.VPN_HOST, xray.VPN_HTTP_PORT, timeout=0.2):
                continue
            ensure_active()

    _watchdog_thread = threading.Thread(
        target=_loop,
        name="asoplay-vpn-watchdog",
        daemon=True,
    )
    _watchdog_thread.start()


def stop_watchdog() -> None:
    """Остановить сторожа и дождаться его потока (до 2 с)."""
    _watchdog_stop.set()
    if _watchdog_thread and _watchdog_thread.is_alive():
        _watchdog_thread.join(timeout=2)


def shutdown() -> None:
    """Остановить сторожа и xray, убрать переменные прокси моста."""
    global _xray_proc, _bridge_desired
    _bridge_desired = False
    stop_watchdog()
    if _xray_proc and _xray_proc.poll() is None:
        try:
            _xray_proc.terminate()
            _xray_proc.wait(timeout=3)
        except OSError:
            try:
                _xray_proc.kill()
            except OSError:
                pass
    _xray_proc = None
    clear_env()


def is_active() -> bool:
    """True while the bridge is wanted and its HTTP proxy port answers."""
    return is_enabled() and _bridge_desired and xray.port_alive(xray.VPN_HOST, xray.VPN_HTTP_PORT, timeout=0.25)


def proxy_url() -> str | None:
    """The bridge's HTTP proxy for a fallback request, or None when it is off
    or down (a background restart is then scheduled if the bridge is wanted)."""
    if is_active():
        return VPN_PROXY_URL
    ensure_active(blocking=False)
    return None
