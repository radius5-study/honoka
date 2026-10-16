from collections.abc import Sequence
from urllib.parse import urlsplit

from honoka import Context, HTTPError, Middleware, Next, Response

_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
_FORM_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data", "text/plain"})


def _same_origin(c: Context, allowed: frozenset[str]) -> bool:
    origin = c.req.header("origin")
    if origin is not None and origin in allowed:
        return True
    site = c.req.header("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    host = c.req.header("host")
    return origin is not None and host is not None and urlsplit(origin).netloc == host


def csrf(*, origins: Sequence[str] = ()) -> Middleware:
    """Reject cross-origin form submissions (the requests a browser sends without a preflight).

    Only unsafe methods with a form-like content type are checked, as in Hono. `origins` lists
    extra origins (`https://other.example`) that may submit forms."""
    allowed = frozenset(origins)

    async def middleware(c: Context, call_next: Next) -> Response:
        content_type = (c.req.header("content-type") or "").split(";")[0].strip().lower()
        if (
            c.req.method not in _SAFE
            and content_type in _FORM_TYPES
            and not _same_origin(c, allowed)
        ):
            raise HTTPError(403, "csrf_rejected")
        return await call_next()

    return middleware
