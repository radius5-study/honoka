from honoka import Context, Middleware, Next, Response


def trim_trailing_slash() -> Middleware:
    """Redirect `GET /users/` to `/users` (301, query kept), but only when `/users/` is a 404."""

    async def middleware(c: Context, call_next: Next) -> Response:
        response = await call_next()
        path = c.req.path
        redirectable = (
            response.status == 404
            and c.req.method in ("GET", "HEAD")
            and len(path) > 1
            and path.endswith("/")
        )
        if not redirectable:
            return response
        # Collapse leading slashes: "//evil.test/" must not become a protocol-relative URL.
        location = "/" + path.rstrip("/").lstrip("/")
        if c.req.query_string:
            location += "?" + c.req.query_string
        return c.redirect(location, 301)

    return middleware
