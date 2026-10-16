from collections.abc import Sequence

from honoka import Context, Middleware, Next, Response

DEFAULT_METHODS = ("GET", "HEAD", "PUT", "POST", "DELETE", "PATCH")

type Pairs = tuple[tuple[str, str], ...]


def cors(
    *,
    origins: Sequence[str],
    methods: Sequence[str] = DEFAULT_METHODS,
    headers: Sequence[str] | None = None,
    expose: Sequence[str] = (),
    credentials: bool = False,
    max_age: int | None = None,
) -> Middleware:
    """CORS. Preflights are answered before routing. `headers=None` echoes the requested headers.

    The headers added for each allowed origin are rendered once here, not on every request."""
    wildcard = "*" in origins
    if wildcard and credentials:
        raise ValueError("credentials cannot be combined with a wildcard origin")

    tail: Pairs = ()
    if credentials:
        tail += (("access-control-allow-credentials", "true"),)
    simple_tail = tail
    if expose:
        simple_tail += (("access-control-expose-headers", ", ".join(expose)),)

    def head(origin: str) -> Pairs:
        if wildcard:
            return (("access-control-allow-origin", "*"),)
        return (("access-control-allow-origin", origin), ("vary", "Origin"))

    simple: dict[str, Pairs] = {o: head(o) + simple_tail for o in origins}
    wildcard_simple = head("*") + simple_tail
    preflight_head: dict[str, Pairs] = {o: head(o) + tail for o in origins}
    wildcard_preflight_head = head("*") + tail
    preflight_tail: Pairs = (("access-control-allow-methods", ", ".join(methods)),)
    if headers is not None:
        preflight_tail += (("access-control-allow-headers", ", ".join(headers)),)
    if max_age is not None:
        preflight_tail += (("access-control-max-age", str(max_age)),)

    async def middleware(c: Context, call_next: Next) -> Response:
        origin = c.req.header("origin")
        if origin is None:
            return await call_next()
        pairs = wildcard_simple if wildcard else simple.get(origin)
        if c.req.method == "OPTIONS" and c.req.header("access-control-request-method") is not None:
            preflight = Response(b"", 204)
            opening = wildcard_preflight_head if wildcard else preflight_head.get(origin)
            if opening is not None:
                preflight.headers.extend(opening)
                preflight.headers.extend(preflight_tail)
                requested = c.req.header("access-control-request-headers")
                if headers is None and requested is not None and requested != "":
                    preflight.headers.append(("access-control-allow-headers", requested))
                    preflight.headers.append(("vary", "Access-Control-Request-Headers"))
            return preflight
        response = await call_next()
        if pairs is not None:
            response.headers.extend(pairs)
        return response

    return middleware
