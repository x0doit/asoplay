# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

VPN bridge — builds an xray runtime config from vpn.template.json + .env and
launches a dedicated xray.exe on a pair of local ports. Upstream targets
(Jikan, AniList, Shikimori, translate) that are blocked in some regions then
become reachable through HTTP_PROXY/HTTPS_PROXY.

This module is side-effect free at import time. The app calls `activate()`
from the FastAPI startup hook, not during module loading. That keeps imports
cheap and lets tests / migrations pull in the server package without spawning
a subprocess.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("animeviev.vpn")


VPN_HOST = "127.0.0.1"
VPN_HTTP_PORT = 20809
VPN_SOCKS_PORT = 20808
VPN_METRICS_PORT = 21111
VPN_PROXY_URL = f"http://{VPN_HOST}:{VPN_HTTP_PORT}"
VPN_SOCKS_URL = f"socks5://{VPN_HOST}:{VPN_SOCKS_PORT}"
_BRIDGE_PROXY_VALUES = {VPN_PROXY_URL, VPN_SOCKS_URL}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
VPN_TEMPLATE = _PROJECT_ROOT / "vpn.template.json"
VPN_RUNTIME = _PROJECT_ROOT / ".xray-runtime.json"
VPN_LOG = _PROJECT_ROOT / ".xray.log"

_XRAY_CANDIDATES = [
    r"C:\Program Files\FlyFrogLLC\Happ\core\xray.exe",
    r"C:\Program Files\Mori VPN\xray.exe",
    r"C:\Program Files\v2rayN\xray.exe",
    r"C:\Program Files (x86)\v2rayN\xray.exe",
]
_LEGACY_PATHS = [
    str(Path.home() / ".animeviev" / "vpn.json"),
    str(_PROJECT_ROOT / "vpn.txt"),
]

_xray_proc: subprocess.Popen | None = None
_bridge_desired = False
_restart_lock = threading.Lock()
_watchdog_stop = threading.Event()
_watchdog_thread: threading.Thread | None = None


def _restart_bridge_locked() -> bool:
    if _port_alive(VPN_HOST, VPN_HTTP_PORT, timeout=0.25):
        _export_env()
        return True
    log.warning("VPN proxy env points to %s, but the port is down; restarting bridge", VPN_PROXY_URL)
    clear_env()
    return activate()


def _restart_bridge_async() -> None:
    if _restart_lock.locked():
        return

    def _run() -> None:
        with _restart_lock:
            _restart_bridge_locked()

    threading.Thread(
        target=_run,
        name="animeviev-vpn-restart",
        daemon=True,
    ).start()


def _port_alive(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_xray() -> str | None:
    for path in _XRAY_CANDIDATES:
        if os.path.isfile(path):
            return path
    return shutil.which("xray") or shutil.which("xray.exe")


def _build_config() -> dict | None:
    ss_addr = os.environ.get("SS_ADDRESS", "").strip()
    ss_pass = os.environ.get("SS_PASSWORD", "").strip()
    if VPN_TEMPLATE.is_file() and ss_addr and ss_pass:
        try:
            cfg = json.loads(VPN_TEMPLATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("vpn.template.json is not valid JSON: %s", exc)
            return None
        try:
            ss_port = int(os.environ.get("SS_PORT") or 0)
        except ValueError:
            ss_port = 0
        ss_method = os.environ.get("SS_METHOD", "chacha20-ietf-poly1305")
        for outbound in cfg.get("outbounds", []):
            if outbound.get("tag") == "proxy" and outbound.get("protocol") == "shadowsocks":
                servers = (outbound.get("settings") or {}).get("servers") or []
                if servers:
                    servers[0]["address"] = ss_addr
                    servers[0]["password"] = ss_pass
                    servers[0]["method"] = ss_method
                    if ss_port:
                        servers[0]["port"] = ss_port
                break
        log.info("VPN config prepared from template + .env (SS host %s)", ss_addr)
        return cfg

    extra = os.environ.get("AV_VPN_CONFIG", "").strip()
    paths = [extra] if extra else []
    paths += _LEGACY_PATHS
    for candidate in paths:
        if candidate and Path(candidate).is_file():
            try:
                cfg = json.loads(Path(candidate).read_text(encoding="utf-8"))
                log.info("VPN config loaded from legacy path %s", candidate)
                return cfg
            except json.JSONDecodeError as exc:
                log.warning("legacy vpn config at %s is not JSON: %s", candidate, exc)
    return None


def _prepare_runtime() -> Path | None:
    cfg = _build_config()
    if cfg is None:
        log.warning(
            "VPN config not available — expected vpn.template.json + .env "
            "(SS_ADDRESS/SS_PASSWORD) or a legacy vpn.json. Falling back to "
            "direct connection."
        )
        return None
    for inbound in cfg.get("inbounds", []):
        if inbound.get("protocol") == "socks":
            inbound["port"] = VPN_SOCKS_PORT
        elif inbound.get("protocol") == "http":
            inbound["port"] = VPN_HTTP_PORT
        elif inbound.get("protocol") == "dokodemo-door":
            inbound["port"] = VPN_METRICS_PORT
    VPN_RUNTIME.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return VPN_RUNTIME


def _launch_xray(config_path: Path) -> subprocess.Popen | None:
    xray = _find_xray()
    if not xray:
        log.warning("xray.exe not found — install Happ or v2rayN, or put xray in PATH")
        return None
    try:
        logf = open(VPN_LOG, "ab", buffering=0)
        creation = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        proc = subprocess.Popen(
            [xray, "-config", str(config_path)],
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            creationflags=creation,
        )
    except OSError as exc:
        log.warning("failed to spawn xray: %s", exc)
        return None

    deadline = time.time() + 8
    while time.time() < deadline:
        if _port_alive(VPN_HOST, VPN_HTTP_PORT):
            log.info("xray up (pid=%s) — VPN on %s", proc.pid, VPN_PROXY_URL)
            return proc
        if proc.poll() is not None:
            log.warning("xray exited early (code=%s) — see %s", proc.returncode, VPN_LOG)
            return None
        time.sleep(0.25)
    log.warning("xray started but proxy port %s did not come up", VPN_HTTP_PORT)
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except OSError:
        pass
    return None


def activate() -> bool:
    """Spin up xray and export HTTP_PROXY/HTTPS_PROXY. Returns True if the
    proxy is up and outbound traffic is now routed through it."""
    global _xray_proc, _bridge_desired
    if _port_alive(VPN_HOST, VPN_HTTP_PORT):
        _bridge_desired = True
        log.info("VPN already listening on %s (reusing)", VPN_PROXY_URL)
        _export_env()
        return True
    config_path = _prepare_runtime()
    if config_path is None:
        _bridge_desired = False
        clear_env()
        return False
    _bridge_desired = True
    _xray_proc = _launch_xray(config_path)
    if _xray_proc is None:
        clear_env()
        return False
    atexit.register(shutdown)
    _export_env()
    return True


def _export_env() -> None:
    os.environ["HTTP_PROXY"] = VPN_PROXY_URL
    os.environ["HTTPS_PROXY"] = VPN_PROXY_URL
    os.environ["ALL_PROXY"] = VPN_SOCKS_URL
    no_proxy = [x.strip() for x in os.environ.get("NO_PROXY", "").split(",") if x.strip()]
    for host in ("127.0.0.1", "localhost"):
        if host not in no_proxy:
            no_proxy.append(host)
    os.environ["NO_PROXY"] = ",".join(no_proxy)


def _env_uses_bridge() -> bool:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        value = (os.environ.get(key) or "").rstrip("/")
        if value in _BRIDGE_PROXY_VALUES:
            return True
    return False


def clear_env() -> None:
    """Remove only proxy variables owned by this bridge.

    A dead local xray port must not poison future httpx clients. Custom user
    proxy variables are left untouched.
    """
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        value = (os.environ.get(key) or "").rstrip("/")
        if value in _BRIDGE_PROXY_VALUES:
            os.environ.pop(key, None)


def ensure_active(*, blocking: bool = True) -> bool:
    """Keep the app-wide proxy env in sync with the local xray port.

    If the app exported 127.0.0.1:20809 earlier and the port later died, try to
    restart xray once for the current request. If that fails, clear the app-owned
    proxy env so callers can fall back to direct outbound instead of waiting on
    a dead local proxy.
    """
    if _port_alive(VPN_HOST, VPN_HTTP_PORT, timeout=0.25):
        _export_env()
        return True
    if not (_bridge_desired or _env_uses_bridge()):
        clear_env()
        return False
    if not blocking:
        clear_env()
        _restart_bridge_async()
        return False
    with _restart_lock:
        return _restart_bridge_locked()


def start_watchdog() -> None:
    """Start a lightweight supervisor for the local xray bridge."""
    global _watchdog_thread
    if _watchdog_thread and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()
    interval = max(0.25, float(os.environ.get("AV_VPN_WATCHDOG_INTERVAL", "0.5")))

    def _loop() -> None:
        while not _watchdog_stop.wait(interval):
            if not _bridge_desired and not _env_uses_bridge():
                continue
            if _port_alive(VPN_HOST, VPN_HTTP_PORT, timeout=0.2):
                continue
            ensure_active()

    _watchdog_thread = threading.Thread(
        target=_loop,
        name="animeviev-vpn-watchdog",
        daemon=True,
    )
    _watchdog_thread.start()


def stop_watchdog() -> None:
    _watchdog_stop.set()
    if _watchdog_thread and _watchdog_thread.is_alive():
        _watchdog_thread.join(timeout=2)


def shutdown() -> None:
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
    """Quick probe for the HTTP_PROXY being set AND the port being alive."""
    active = bool(os.environ.get("HTTPS_PROXY")) and _port_alive(VPN_HOST, VPN_HTTP_PORT)
    if not active and _env_uses_bridge():
        clear_env()
    return active
