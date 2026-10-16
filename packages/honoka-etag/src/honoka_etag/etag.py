import hashlib
from collections.abc import Callable, Sequence

from honoka import Context, FileResponse, Middleware, Next, Response, Stream

RETAINED_304_HEADERS = (
    "cache-control",
    "content-location",
    "date",
    "etag",
    "expires",
    "vary",
)


def _sha1(body: bytes) -> bytes:
    return hashlib.sha1(body, usedforsecurity=False).digest()


def _opaque(tag: str) -> str:
    tag = tag.strip()
    return tag[2:] if tag[:2] in ("W/", "w/") else tag


def _matches(if_none_match: str, etag: str) -> bool:
    """Weak comparison of an `If-None-Match` list against `etag`."""
    wanted = _opaque(etag)
    return any(t.strip() == "*" or _opaque(t) == wanted for t in if_none_match.split(","))


def etag(
    *,
    weak: bool = False,
    retained_headers: Sequence[str] = RETAINED_304_HEADERS,
    generate_digest: Callable[[bytes], bytes] = _sha1,
) -> Middleware:
    """ETag, as Hono's `etag`: tags buffered 200 GET/HEAD responses and answers 304 when
    `If-None-Match` matches. A 304 keeps only `retained_headers`; an ETag the handler already
    set is kept (and used for the 304 check) rather than recomputed. Streams and files, which
    have no buffered body, and empty bodies are left alone."""
    keep = frozenset(h.lower() for h in retained_headers)

    async def middleware(c: Context, call_next: Next) -> Response:
        response = await call_next()
        if (
            c.req.method not in ("GET", "HEAD")
            or response.status != 200
            or isinstance(response, Stream | FileResponse)
            or not response.body
        ):
            return response
        tag = next((v for k, v in response.headers if k.lower() == "etag"), None)
        if tag is None:
            digest = generate_digest(response.body).hex()
            tag = f'W/"{digest}"' if weak else f'"{digest}"'
            response.headers.append(("etag", tag))
        condition = c.req.header("if-none-match")
        if condition is not None and _matches(condition, tag):
            return Response(b"", 304, [h for h in response.headers if h[0].lower() in keep])
        return response

    return middleware
