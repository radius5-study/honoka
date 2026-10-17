from urllib.parse import quote

from honoka import Context, Middleware, Next, Response

_SAFE = "/:@!$&'()*+,;=-._~"


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
        # The path is decoded text. Re-encode it so a backslash or a tab cannot make the Location
        # read as another origin, and collapse leading slashes so "//evil.test/" stays on this one.
        location = "/" + quote(path.strip("/"), safe=_SAFE)
        if c.req.query_string:
            location += "?" + c.req.query_string
        return c.redirect(location, 301)

    return middleware
