from collections.abc import Sequence

from honoka import Context, Middleware, Next, Response

DEFAULT_METHODS = ("GET", "HEAD", "PUT", "POST", "DELETE", "PATCH")


def cors(
    *,
    origins: Sequence[str],
    methods: Sequence[str] = DEFAULT_METHODS,
    headers: Sequence[str] | None = None,
    expose: Sequence[str] = (),
    credentials: bool = False,
    max_age: int | None = None,
) -> Middleware:
    """CORS. Preflights are answered before routing. `headers=None` echoes the requested headers."""
    wildcard = "*" in origins
    if wildcard and credentials:
        raise ValueError("credentials cannot be combined with a wildcard origin")
    allowed = frozenset(origins)

    def decorate(response: Response, origin: str) -> None:
        response.headers.append(("access-control-allow-origin", "*" if wildcard else origin))
        if not wildcard:
            response.headers.append(("vary", "Origin"))
        if credentials:
            response.headers.append(("access-control-allow-credentials", "true"))

    async def middleware(c: Context, call_next: Next) -> Response:
        origin = c.req.header("origin")
        accepted = origin if origin is not None and (wildcard or origin in allowed) else None
        requested_method = c.req.header("access-control-request-method")
        if origin is not None and c.req.method == "OPTIONS" and requested_method is not None:
            preflight = Response(b"", 204)
            if accepted is not None:
                decorate(preflight, accepted)
                preflight.headers.append(("access-control-allow-methods", ", ".join(methods)))
                requested = c.req.header("access-control-request-headers")
                allow_headers = ", ".join(headers) if headers is not None else requested
                if allow_headers is not None and allow_headers != "":
                    preflight.headers.append(("access-control-allow-headers", allow_headers))
                    if headers is None:
                        preflight.headers.append(("vary", "Access-Control-Request-Headers"))
                if max_age is not None:
                    preflight.headers.append(("access-control-max-age", str(max_age)))
            return preflight
        response = await call_next()
        if accepted is not None:
            decorate(response, accepted)
            if expose:
                response.headers.append(("access-control-expose-headers", ", ".join(expose)))
        return response

    return middleware
