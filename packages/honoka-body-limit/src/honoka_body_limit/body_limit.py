from honoka import Context, HTTPError, Middleware, Next, Response


def body_limit(max_bytes: int) -> Middleware:
    """Answer 413 for bodies over `max_bytes`: by Content-Length up front, and by byte count
    while the body is read when the client sent no (or a false) length."""

    async def middleware(c: Context, call_next: Next) -> Response:
        declared = c.req.header("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise HTTPError(413, "payload_too_large")
        c.req.limit_body(max_bytes)
        return await call_next()

    return middleware
