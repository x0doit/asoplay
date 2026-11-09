"""
Выход через VPN-мост к проверенному адресу: сетевой слой httpcore для клиента картинок через мост.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Мост нужен как раз тем хостам, до которых сервер не достаёт сам, — в том числе когда свой DNS сервера для них врёт
или молчит. Поэтому имя здесь разрешает не свой DNS, а DoH через сам мост: запрос идёт на IP из DOH_SERVERS по
очереди, с проверкой сертификата, со своим сроком DOH_TIMEOUT — отдельно от срока подключения. A и AAAA спрашиваются
сразу оба; на одно имя — одна выборка, и отмена запроса её не обрывает: ответ ляжет в кеш для следующей попытки.
Ответ живёт в кеше по своему TTL, но не меньше TTL_MIN и не больше TTL_MAX. Все адреса проверяются, как в
core/classes/http_public.py: хоть один не публичный — Refused.

Мост получает CONNECT ровно на проверенный адрес (сначала IPv4). Выход моста подключается к тому же адресу, только
если xray не подменяет назначение именем из TLS: рабочий конфиг моста ставит routeOnly всегда, а чужой уже
работающий xray может быть со старым конфигом — через такой мост слой не ходит (vpn_bridge.destinations_pinned).
TLS и Host идут по имени из запроса: SNI и проверку сертификата httpcore берёт из адреса запроса, а не из адреса
подключения. Адрес-IP в запросе (сами серверы DoH) не разрешается, только проверяется. Поток к мосту закрывается при
любом сбое и отмене подключения.

Мост выключен или не привязывает назначение, DoH не ответил, имя не разрешилось — httpcore.ConnectError: сетевой
сбой этого пути, не отказ.
"""
from __future__ import annotations

import asyncio
import ipaddress
import time
import typing
from urllib.parse import urlsplit

import anyio
import httpcore
import httpx

from core.classes import http_public
from core.classes.vpn import bridge as vpn_bridge

DOH_SERVERS = ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve")
DOH_TIMEOUT = 6.0
TTL_MIN = 30
TTL_MAX = 300
_RECORDS = ((1, "A"), (28, "AAAA"))
_NAMES_MAX = 1000
_HEAD_MAX = 16 * 1024
_TRIES = 2  # адресов на одно подключение
_TUNNEL_ERRORS = (httpcore.ConnectError, httpcore.ReadError, httpcore.WriteError, httpcore.TimeoutException)

_names: dict[str, tuple[float, list[str]]] = {}  # имя → (до какого времени, адреса)
_resolving: dict[str, asyncio.Task] = {}
_doh: httpx.AsyncClient | None = None


def _is_ip(host: str) -> bool:
    """IP-адрес ли это, а не имя."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _doh_client() -> httpx.AsyncClient:
    """Клиент DoH: тот же выход через мост, без кук; серверы DoH — по IP, сертификат проверяется по нему."""
    global _doh
    if _doh is None or _doh.is_closed:
        _doh = httpx.AsyncClient(transport=transport(trust_env=False), trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(DOH_TIMEOUT, connect=DOH_TIMEOUT))
    return _doh


async def _ask(server: str, name: str, code: int, kind: str) -> tuple[list[str], int]:
    """Записи одного типа у одного сервера DoH и наименьший TTL; нет имени — пустой список."""
    resp = await _doh_client().get(server, params={"name": name, "type": kind},
                                   headers={"accept": "application/dns-json"})
    resp.raise_for_status()
    answer = resp.json()
    if answer.get("Status") not in (0, 3):  # 3 — NXDOMAIN, остальное — сбой сервера
        raise ValueError(f"DoH status {answer.get('Status')}")
    addresses, ttl = [], TTL_MAX
    for record in answer.get("Answer") or []:
        if isinstance(record, dict) and record.get("type") == code:
            addresses.append(str(ipaddress.ip_address(str(record.get("data", "")))))
            ttl = min(ttl, int(record.get("TTL", TTL_MIN)))
    return addresses, ttl


async def _lookup(server: str, name: str) -> tuple[list[str], int]:
    """Адреса имени (A и AAAA разом) у одного сервера DoH и наименьший TTL. Сбой — httpx.HTTPError или ValueError."""
    answers = await asyncio.gather(*(_ask(server, name, code, kind) for code, kind in _RECORDS))
    return [a for found, _ in answers for a in found], min(ttl for _, ttl in answers)


async def _resolve_once(name: str) -> list[str]:
    """Спросить имя у серверов DoH по очереди, в пределах DOH_TIMEOUT; проверить адреса и положить в кеш."""
    failure: Exception | None = None
    with anyio.move_on_after(DOH_TIMEOUT):
        for server in DOH_SERVERS:
            try:
                addresses, ttl = await _lookup(server, name)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                failure = exc
                continue
            if not addresses:
                raise httpcore.ConnectError(f"{name} has no addresses")
            blocked = sorted({a for a in addresses if not http_public.is_public(a)})
            if blocked:
                raise http_public.Refused(f"{name} resolves to non-public {', '.join(blocked)}")
            addresses.sort(key=lambda a: ":" in a)
            if len(_names) >= _NAMES_MAX:
                _names.clear()
            _names[name] = (time.monotonic() + min(max(ttl, TTL_MIN), TTL_MAX), addresses)
            return addresses
    raise httpcore.ConnectError(f"DoH failed for {name}: {failure or 'timed out'}")


async def resolve(name: str) -> list[str]:
    """Адреса имени через DoH по мосту, IPv4 первыми. Хоть один не публичный — Refused; не разрешилось —
    httpcore.ConnectError. Одна выборка на имя; отмена ожидающего её не обрывает."""
    known = _names.get(name)
    if known and known[0] > time.monotonic():
        return known[1]
    task = _resolving.get(name)
    if task is None:
        task = _resolving[name] = asyncio.ensure_future(_resolve_once(name))

        def finished(done: asyncio.Task) -> None:
            """Убрать выборку из идущих и забрать её исключение, чтобы asyncio не писал его в журнал."""
            _resolving.pop(name, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
    return await asyncio.shield(task)


async def _tunnel(stream: httpcore.AsyncNetworkStream, address: str, port: int, timeout: float | None) -> None:
    """CONNECT на адрес через мост; мост ответил не 200 или прислал лишнее — httpcore.ConnectError."""
    target = f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
    await stream.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"), timeout=timeout)
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = await stream.read(4096, timeout=timeout)
        if not chunk:
            raise httpcore.ConnectError("the bridge closed the tunnel")
        head += chunk
        if len(head) > _HEAD_MAX:
            raise httpcore.ConnectError("the bridge answer is too long")
    status = head.split(b"\r\n", 1)[0].split(b" ")
    if len(status) < 2 or status[1] != b"200":
        raise httpcore.ConnectError(f"the bridge refused CONNECT {target}: {head[:60]!r}")
    if not head.endswith(b"\r\n\r\n"):
        raise httpcore.ConnectError("the bridge sent data before the request")


class BridgeBackend(httpcore.AsyncNetworkBackend):
    """Сетевой слой пула: имя — через DoH по мосту, подключение — CONNECT через мост к проверенному адресу."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        """inner — обычный слой пула; через него идёт подключение к самому мосту."""
        self._inner = inner

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None,
                          socket_options: typing.Iterable[typing.Any] | None = None) -> httpcore.AsyncNetworkStream:
        """Туннель через мост к адресу host. Имя разрешается со своим сроком DOH_TIMEOUT, туннель — в timeout."""
        proxy = vpn_bridge.proxy_url()
        if proxy is None or not vpn_bridge.destinations_pinned():
            raise httpcore.ConnectError("the VPN bridge is down or does not pin destinations")
        if _is_ip(host):
            if not http_public.is_public(host):
                raise http_public.Refused(f"{host} is not a public address")
            addresses = [host]
        else:
            addresses = await resolve(host)
        parts = urlsplit(proxy)
        try:
            with anyio.fail_after(timeout):
                return await self._first(parts.hostname or "127.0.0.1", parts.port or 80, addresses, port,
                                         timeout, socket_options)
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"connect to {host} through the bridge timed out") from exc

    async def _first(self, proxy_host: str, proxy_port: int, addresses: list[str], port: int, timeout: float | None,
                     socket_options: typing.Iterable[typing.Any] | None) -> httpcore.AsyncNetworkStream:
        """Первый адрес, до которого мост дотянулся; не больше _TRIES попыток. Поток неудачной или отменённой
        попытки закрывается всегда — иначе зависший мост копил бы открытые сокеты."""
        failure: Exception | None = None
        for address in addresses[:_TRIES]:
            stream = await self._inner.connect_tcp(proxy_host, proxy_port, timeout=timeout, socket_options=socket_options)
            try:
                await _tunnel(stream, address, port, timeout)
            except BaseException as exc:
                with anyio.CancelScope(shield=True):
                    await stream.aclose()
                if not isinstance(exc, _TUNNEL_ERRORS):
                    raise
                failure = exc
                continue
            return stream
        raise httpcore.ConnectError(f"the bridge could not reach {', '.join(addresses[:_TRIES])}: {failure}")

    async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                  socket_options: typing.Iterable[typing.Any] | None = None
                                  ) -> httpcore.AsyncNetworkStream:
        """Сокеты Unix — путь внутрь сервера; клиенту моста они не нужны."""
        raise http_public.Refused(f"unix socket {path} is not a public address")

    async def sleep(self, seconds: float) -> None:
        """Пауза между повторами — как у обычного слоя."""
        await self._inner.sleep(seconds)


def transport(**kwargs: typing.Any) -> httpx.AsyncHTTPTransport:
    """Транспорт httpx через мост к проверенным адресам; kwargs — как у AsyncHTTPTransport, без proxy."""
    if kwargs.get("proxy"):
        raise ValueError("http_bridge.transport takes no proxy: it reaches the bridge itself")
    out = httpx.AsyncHTTPTransport(**kwargs)
    pool = out._pool  # входа для сетевого слоя у httpx нет — как в core/classes/http_public.py
    pool._network_backend = BridgeBackend(pool._network_backend)
    return out


async def shutdown() -> None:
    """Закрыть клиент DoH, отменить идущие выборки имён и забыть разрешённые имена."""
    global _doh
    pending = list(_resolving.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _resolving.clear()
    if _doh is not None:
        await _doh.aclose()
        _doh = None
    _names.clear()
