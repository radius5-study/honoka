import inspect
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network

from honoka import Context, HTTPError, Middleware, Next, Response

type Network = IPv4Network | IPv6Network
type GetIp = Callable[[Context], str | None]
type OnBlocked = Callable[[str | None, Context], Response | Awaitable[Response]]

_ALL: tuple[Network, ...] = (IPv4Network("0.0.0.0/0"), IPv6Network("::/0"))


def client_ip(c: Context) -> str | None:
    """The peer address from the ASGI scope (`host:port`, split on the last colon)."""
    host, sep, _ = c.req.client.rpartition(":")
    return host if sep and host else None


def _rules(rules: Sequence[str]) -> list[Network]:
    out: list[Network] = []
    for rule in rules:
        if rule == "*":
            out.extend(_ALL)
        else:
            out.append(ip_network(rule, strict=False))
    return out


def _parse(addr: str | None) -> IPv4Address | IPv6Address | None:
    if addr is None:
        return None
    try:
        return ip_address(addr.partition("%")[0])
    except ValueError:
        return None


def _matches(ip: IPv4Address | IPv6Address, networks: list[Network]) -> bool:
    candidates = [ip]
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        candidates.append(ip.ipv4_mapped)  # ::ffff:1.2.3.4 is also the IPv4 peer 1.2.3.4
    return any(candidate in net for candidate in candidates for net in networks)


def ip_restriction(
    *,
    allow_list: Sequence[str] = (),
    deny_list: Sequence[str] = (),
    get_ip: GetIp = client_ip,
    on_blocked: OnBlocked | None = None,
) -> Middleware:
    """IP allow/deny lists, as Hono's `ipRestriction`.

    Rules are IPv4/IPv6 addresses, CIDR ranges, or `*`. The deny list is checked first and
    wins; then an address in the allow list passes; otherwise it passes only when the allow list
    is empty. An address that cannot be determined is blocked (fail closed). `get_ip` replaces Hono's
    `getConnInfo`; `on_blocked(addr, c)` replaces the default 403 `HTTPError`."""
    deny = _rules(deny_list)
    allow = _rules(allow_list)

    async def middleware(c: Context, call_next: Next) -> Response:
        addr = get_ip(c)
        ip = _parse(addr)
        if ip is not None and not _matches(ip, deny) and (_matches(ip, allow) or not allow):
            return await call_next()
        if on_blocked is None:
            raise HTTPError(403, "forbidden", "ip address is not allowed")
        result = on_blocked(addr, c)
        return await result if inspect.isawaitable(result) else result

    return middleware
