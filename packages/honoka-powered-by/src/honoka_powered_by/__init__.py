from honoka import Context, Middleware, Next, Response


def powered_by(server_name: str = "honoka") -> Middleware:
    """Add `X-Powered-By: <server_name>` to every response."""

    async def middleware(c: Context, call_next: Next) -> Response:
        response = await call_next()
        response.headers.append(("x-powered-by", server_name))
        return response

    return middleware


__all__ = ["powered_by"]
