import gzip
import re
import zlib
from collections.abc import Callable
from typing import Literal

from honoka import Context, FileResponse, Middleware, Next, Response, Stream

type Encoding = Literal["gzip", "deflate"]
type ContentTypeFilter = re.Pattern[str] | Callable[[str], bool]

# Hono's list of compressible content types.
COMPRESSIBLE_CONTENT_TYPE_REGEX = re.compile(
    r"^\s*(?:text/[^;\s]+"
    r"|application/(?:javascript|json|xml|xml-dtd|ecmascript|dart|postscript|rtf|tar|toml"
    r"|vnd\.dart|vnd\.ms-fontobject|wasm|x-httpd-php|x-javascript|x-ns-proxy-autoconfig"
    r"|x-sh|x-tar|x-virtualbox-hdd|x-virtualbox-ova|x-virtualbox-ovf|x-virtualbox-vbox"
    r"|x-virtualbox-vdi|x-virtualbox-vhd|x-virtualbox-vmdk|x-www-form-urlencoded)"
    r"|font/(?:otf|ttf)"
    r"|image/(?:bmp|vnd\.adobe\.photoshop|vnd\.microsoft\.icon|vnd\.ms-dds|x-icon|x-ms-bmp)"
    r"|message/rfc822|model/gltf-binary|x-shader/x-fragment|x-shader/x-vertex"
    r"|[^;\s]+?\+(?:json|text|xml|yaml))(?:[;\s]|$)",
    re.IGNORECASE,
)


def _accepted(header: str, allowed: tuple[Encoding, ...]) -> Encoding | None:
    """The allowed encoding with the highest q (ties go to the earlier one); `*` covers the
    rest, `q=0` refuses."""
    weights: dict[str, float] = {}
    for part in header.split(","):
        name, *params = (p.strip() for p in part.split(";"))
        q = 1.0
        for param in params:
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        weights[name.lower()] = q
    best: Encoding | None = None
    best_q = 0.0
    for enc in allowed:
        q = weights.get(enc, weights.get("*", 0.0))
        if q > best_q:
            best, best_q = enc, q
    return best


def compress(
    *,
    encoding: Encoding | None = None,
    threshold: int = 1024,
    content_type_filter: ContentTypeFilter = COMPRESSIBLE_CONTENT_TYPE_REGEX,
) -> Middleware:
    """Response compression, as Hono's `compress`: gzip or deflate, picked from `Accept-Encoding`
    (q-values honoured, gzip preferred on a tie; `encoding` pins one).

    Buffered bodies of at least `threshold` bytes with a compressible content type are
    compressed; `Content-Length` is dropped, `Vary: Accept-Encoding` added and a strong ETag
    made weak. Skipped for HEAD, streams, files, an existing `Content-Encoding` and
    `Cache-Control: no-transform`, and 206 partial responses. Brotli and zstd are not implemented (no dependency)."""
    allowed: tuple[Encoding, ...] = ("gzip", "deflate") if encoding is None else (encoding,)

    def compressible(content_type: str) -> bool:
        if isinstance(content_type_filter, re.Pattern):
            return content_type_filter.search(content_type) is not None
        return content_type_filter(content_type)

    async def middleware(c: Context, call_next: Next) -> Response:
        response = await call_next()
        header = c.req.header("accept-encoding")
        if (
            header is None
            or c.req.method == "HEAD"
            or isinstance(response, Stream | FileResponse)
            or response.status == 206  # a partial response: its Content-Range describes the bytes
            or len(response.body) < max(threshold, 1)
        ):
            return response
        names = {k.lower(): v for k, v in response.headers}
        content_type = names.get("content-type")
        if (
            "content-encoding" in names
            or "no-transform" in names.get("cache-control", "").lower()
            or content_type is None
            or not compressible(content_type)
        ):
            return response
        chosen = _accepted(header, allowed)
        if chosen is None:
            return response
        response.body = (
            gzip.compress(response.body, mtime=0)
            if chosen == "gzip"
            else zlib.compress(response.body)
        )
        response.headers[:] = [
            (k, f"W/{v}" if k.lower() == "etag" and not v.startswith(("W/", "w/")) else v)
            for k, v in response.headers
            if k.lower() != "content-length"
        ]
        response.headers.append(("content-encoding", chosen))
        response.headers.append(("vary", "Accept-Encoding"))
        return response

    return middleware
