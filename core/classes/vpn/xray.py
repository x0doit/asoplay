"""
xray для VPN-моста: конфиг из шаблона core/config/server/xray.template.json и .env и запуск процесса на локальных портах.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Сервер берётся из ссылки vless:// в AV_VPN_URL (VLESS + Reality) или, в старых установках, из переменных SS_*
(Shadowsocks); без них — из старого vpn.json. Жизненный цикл моста (включён ли, перезапуск, сторож, адрес прокси для
запроса) — в core/classes/vpn/bridge.py: он зовёт эти функции через модуль. При импорте ничего не запускается.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from core.config import config

log = logging.getLogger("asoplay.vpn")


VPN_HOST = "127.0.0.1"
VPN_HTTP_PORT = 20809
VPN_SOCKS_PORT = 20808
VPN_METRICS_PORT = 21111
VPN_PROXY_URL = f"http://{VPN_HOST}:{VPN_HTTP_PORT}"
VPN_SOCKS_URL = f"socks5://{VPN_HOST}:{VPN_SOCKS_PORT}"

_XRAY_CANDIDATES = [
    r"C:\Program Files\FlyFrogLLC\Happ\core\xray.exe",
    r"C:\Program Files\Mori VPN\xray.exe",
    r"C:\Program Files\v2rayN\xray.exe",
    r"C:\Program Files (x86)\v2rayN\xray.exe",
]
_LEGACY_PATHS = [str(path) for path in config.LEGACY_VPN_FILES]


def port_alive(host: str, port: int, timeout: float = 1.0) -> bool:
    """Отвечает ли локальный порт (порт моста поднят)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_xray() -> str | None:
    """Путь к xray: известные установки Happ, Mori VPN, v2rayN или PATH."""
    for path in _XRAY_CANDIDATES:
        if os.path.isfile(path):
            return path
    return shutil.which("xray") or shutil.which("xray.exe")


def _vless_outbound(link: str) -> dict | None:
    """Turn a vless:// share link, as exported by Xray panels and Happ, into the
    "proxy" outbound. Only the TCP transport with Reality, TLS or no security
    is supported; anything else is rejected rather than half-configured."""
    try:
        parts = urlsplit(link)
        port = parts.port
    except ValueError:
        parts, port = None, None
    if not parts or parts.scheme != "vless" or not parts.username or not parts.hostname or not port:
        log.warning("AV_VPN_URL is not a vless://uuid@host:port link; ignoring it")
        return None
    # Share links percent-encode values; unquote keeps a literal "+" intact.
    query = {}
    for pair in parts.query.split("&"):
        key, _, value = pair.partition("=")
        if key:
            query[unquote(key)] = unquote(value)
    security = query.get("security") or "none"
    if query.get("type", "tcp") not in {"tcp", "raw"} or security not in {"reality", "tls", "none"}:
        log.warning("AV_VPN_URL uses an unsupported transport (type=%s, security=%s)",
                    query.get("type"), security)
        return None
    user = {"id": unquote(parts.username), "encryption": query.get("encryption") or "none"}
    if query.get("flow"):
        user["flow"] = query["flow"]
    stream: dict = {"network": "tcp", "security": security}
    if security == "reality":
        if not query.get("pbk"):
            log.warning("AV_VPN_URL uses Reality without a public key (pbk); ignoring it")
            return None
        stream["realitySettings"] = {
            "serverName": query.get("sni", ""),
            "fingerprint": query.get("fp") or "chrome",
            "publicKey": query["pbk"],
            "shortId": query.get("sid", ""),
            "spiderX": query.get("spx", ""),
        }
    elif security == "tls":
        stream["tlsSettings"] = {
            "serverName": query.get("sni") or parts.hostname,
            "fingerprint": query.get("fp") or "chrome",
        }
    return {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {"vnext": [{"address": parts.hostname, "port": port, "users": [user]}]},
        "streamSettings": stream,
    }


def build_config() -> dict | None:
    """Конфиг xray: шаблон и .env (ссылка VLESS или Shadowsocks), иначе старый vpn.json; нет ничего — None."""
    link = config.vpn_url()
    vless = _vless_outbound(link) if link else None
    ss_addr = config.ss_address()
    ss_pass = config.ss_password()
    if config.XRAY_TEMPLATE.is_file() and (vless or (ss_addr and ss_pass)):
        try:
            cfg = json.loads(config.XRAY_TEMPLATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("xray.template.json is not valid JSON: %s", exc)
            return None
        if vless:
            # The proxy outbound stays first: xray sends unmatched traffic to it.
            cfg["outbounds"] = [vless] + [o for o in cfg.get("outbounds", []) if o.get("tag") != "proxy"]
            log.info("VPN config prepared from template + AV_VPN_URL (VLESS host %s)",
                     vless["settings"]["vnext"][0]["address"])
            return cfg
        try:
            ss_port = int(config.ss_port() or 0)
        except ValueError:
            ss_port = 0
        ss_method = config.ss_method()
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

    extra = config.vpn_config_file()
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


def prepare_runtime() -> Path | None:
    """Записать конфиг xray с портами моста в рабочий файл; конфига нет — None."""
    cfg = build_config()
    if cfg is None:
        log.warning(
            "VPN config not available — expected xray.template.json + .env "
            "(AV_VPN_URL or SS_ADDRESS/SS_PASSWORD) or a legacy vpn.json. "
            "Falling back to direct connection."
        )
        return None
    for inbound in cfg.get("inbounds", []):
        if inbound.get("protocol") == "socks":
            inbound["port"] = VPN_SOCKS_PORT
        elif inbound.get("protocol") == "http":
            inbound["port"] = VPN_HTTP_PORT
        elif inbound.get("protocol") == "dokodemo-door":
            inbound["port"] = VPN_METRICS_PORT
    config.XRAY_RUNTIME.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return config.XRAY_RUNTIME


def launch(config_path: Path) -> subprocess.Popen | None:
    """Запустить xray с конфигом и дождаться порта HTTP-прокси (до 8 с); не поднялся — None."""
    xray = _find_xray()
    if not xray:
        log.warning("xray.exe not found — install Happ or v2rayN, or put xray in PATH")
        return None
    try:
        logf = open(config.XRAY_LOG, "ab", buffering=0)
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
        if port_alive(VPN_HOST, VPN_HTTP_PORT):
            log.info("xray up (pid=%s) — VPN on %s", proc.pid, VPN_PROXY_URL)
            return proc
        if proc.poll() is not None:
            log.warning("xray exited early (code=%s) — see %s", proc.returncode, config.XRAY_LOG)
            return None
        time.sleep(0.25)
    log.warning("xray started but proxy port %s did not come up", VPN_HTTP_PORT)
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except OSError:
        pass
    return None
