"""
Выход в сеть только на публичные адреса: сетевой слой httpcore для клиента прокси плеера.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Прокси плеера ходит по адресам из браузера, а имя из белого списка (core/functions/player_hosts.py) само по себе
не говорит, куда придёт подключение. Этот слой разрешает имя сам (A и AAAA) и отказывает (Refused), если хоть один
адрес не публичный: loopback, частные сети, link-local с адресом метаданных 169.254.169.254, CGNAT, multicast,
зарезервированные. Подключается он только к проверенным адресам, в порядке и с задержкой happy eyeballs, как
anyio.connect_tcp. Второго разрешения имени нет, поэтому подменить ответ DNS между проверкой и подключением нельзя.
TLS идёт по имени из адреса: SNI и проверку сертификата httpcore берёт из запроса, а не из адреса подключения.

Слой рассчитан на клиент без прокси: у клиента плеера trust_env=False и нет VPN-моста. Если клиенту плеера
дадут мост, слою придётся пропускать адрес самого моста, а цель проверять отдельно.
"""
from __future__ import annotations

import ipaddress
import socket
import typing

import anyio
import httpcore
import httpx

# Как у anyio.connect_tcp: следующий адрес пробуется, если предыдущий не подключился за это время
_HAPPY_EYEBALLS_DELAY = 0.25
# NAT64 (RFC 6052): последние 32 бита — адрес IPv4, он и проверяется
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class Refused(httpx.ConnectError):
    """Запрос не ушёл: адрес цели не публичный или цель не из белого списка прокси плеера."""


def is_public(address: str) -> bool:
    """Публичный ли адрес IP. IPv4 внутри IPv6 (::ffff:…, NAT64, 6to4) проверяется как IPv4."""
    ip = ipaddress.ip_address(address)
    if ip.version == 6:
        inner = ip.ipv4_mapped or ip.sixtofour or (
            ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF) if ip in _NAT64 else None)
        if inner is not None:
            ip = inner
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


async def public_addresses(host: str, port: int) -> list[str]:
    """Адреса имени (A и AAAA) в порядке попыток anyio: первый IPv6, первый IPv4, дальше остальные.

    Хоть один адрес не публичный — Refused. Имя не разрешилось — httpcore.ConnectError, как у обычного слоя.
    """
    try:
        infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise httpcore.ConnectError(str(exc)) from exc
    if not infos:
        raise httpcore.ConnectError(f"{host} has no addresses")
    blocked = sorted({str(sockaddr[0]) for *_, sockaddr in infos if not is_public(str(sockaddr[0]))})
    if blocked:
        raise Refused(f"{host} resolves to non-public {', '.join(blocked)}")
    out: list[str] = []
    v6_found = v4_found = False
    for family, *_, sockaddr in infos:
        address = str(sockaddr[0])
        if family == socket.AF_INET6 and not v6_found:
            v6_found = True
            out.insert(0, address)
        elif family == socket.AF_INET and not v4_found and v6_found:
            v4_found = True
            out.insert(1, address)
        else:
            out.append(address)
    return out


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Сетевой слой пула: имя разрешается здесь, подключение — только к его проверенным публичным адресам."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        """inner — обычный слой пула; через него идут подключения к уже проверенным адресам."""
        self._inner = inner

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None,
                          socket_options: typing.Iterable[typing.Any] | None = None) -> httpcore.AsyncNetworkStream:
        """Подключение к host: все его адреса публичные — первое удачное к ним, иначе Refused. Разрешение имени
        и подключение укладываются в один timeout, как у обычного слоя."""
        try:
            with anyio.fail_after(timeout):
                addresses = await public_addresses(host, port)
                return await self._first(addresses, port, local_address, socket_options)
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"connect to {host} timed out") from exc

    async def _first(self, addresses: list[str], port: int, local_address: str | None,
                     socket_options: typing.Iterable[typing.Any] | None) -> httpcore.AsyncNetworkStream:
        """Первое удачное подключение: следующий адрес — через _HAPPY_EYEBALLS_DELAY после предыдущего, как у
        anyio.connect_tcp. Все неудачны — ошибка единственной попытки или общая httpcore.ConnectError."""
        winner: list[httpcore.AsyncNetworkStream] = []
        errors: list[httpcore.ConnectError] = []

        async def attempt(address: str, done: anyio.Event) -> None:
            """Одна попытка: удачная первой отменяет остальные, опоздавшая закрывается."""
            try:
                stream = await self._inner.connect_tcp(address, port, local_address=local_address,
                                                       socket_options=socket_options)
            except httpcore.ConnectError as exc:
                errors.append(exc)
            else:
                if winner:
                    await stream.aclose()
                else:
                    winner.append(stream)
                    group.cancel_scope.cancel()
            finally:
                done.set()

        async with anyio.create_task_group() as group:
            for address in addresses:
                done = anyio.Event()
                group.start_soon(attempt, address, done)
                with anyio.move_on_after(_HAPPY_EYEBALLS_DELAY):
                    await done.wait()
        if winner:
            return winner[0]
        if len(errors) == 1:
            raise errors[0]
        raise httpcore.ConnectError(f"all {len(errors)} connection attempts failed")

    async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                  socket_options: typing.Iterable[typing.Any] | None = None
                                  ) -> httpcore.AsyncNetworkStream:
        """Сокеты Unix — путь внутрь сервера; клиенту плеера они не нужны."""
        raise Refused(f"unix socket {path} is not a public address")

    async def sleep(self, seconds: float) -> None:
        """Пауза между повторами — как у обычного слоя."""
        await self._inner.sleep(seconds)


def transport(**kwargs: typing.Any) -> httpx.AsyncHTTPTransport:
    """Транспорт httpx, который подключается только к публичным адресам; kwargs — как у AsyncHTTPTransport.

    Прокси нельзя: подключение шло бы к нему, а проверять нужно цель.
    """
    if kwargs.get("proxy"):
        raise ValueError("http_public.transport takes no proxy: the target address has to be checked")
    out = httpx.AsyncHTTPTransport(**kwargs)
    # У httpx нет публичного входа для сетевого слоя httpcore, поэтому слой ставится в пул транспорта.
    # core/tests/test_player_guard.py проверяет, что он на месте: обновление httpx не снимет проверку молча.
    pool = out._pool
    pool._network_backend = PublicOnlyBackend(pool._network_backend)
    return out
