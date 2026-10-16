import inspect
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

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


def _matches(addr: str | None, networks: list[Network]) -> bool:
    if addr is None:
        return False
    try:
        ip = ip_address(addr.partition("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:  # ::ffff:1.2.3.4 is the IPv4 peer
        ip = ip.ipv4_mapped
    return any(ip in net for net in networks)


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
    is empty. An address that cannot be determined matches nothing. `get_ip` replaces Hono's
    `getConnInfo`; `on_blocked(addr, c)` replaces the default 403 `HTTPError`."""
    deny = _rules(deny_list)
    allow = _rules(allow_list)

    async def middleware(c: Context, call_next: Next) -> Response:
        addr = get_ip(c)
        if not _matches(addr, deny) and (_matches(addr, allow) or not allow):
            return await call_next()
        if on_blocked is None:
            raise HTTPError(403, "forbidden", "ip address is not allowed")
        result = on_blocked(addr, c)
        return await result if inspect.isawaitable(result) else result

    return middleware
