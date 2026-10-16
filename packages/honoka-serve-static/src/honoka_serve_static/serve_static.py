import inspect
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from honoka import Context, HTTPError, Middleware, Next, Response

type Hook = Callable[[str, Context], Awaitable[None] | None]

# Hono's precompressed order: first the client accepts and the disk has wins.
_ENCODINGS = (("br", ".br"), ("zstd", ".zst"), ("gzip", ".gz"))


def serve_static(
    *,
    root: str | os.PathLike[str] = ".",
    path: str | None = None,
    rewrite_request_path: Callable[[str], str] | None = None,
    mimes: Mapping[str, str] | None = None,
    on_found: Hook | None = None,
    on_not_found: Hook | None = None,
    index: str = "index.html",
    cache_control: str | None = None,
    precompressed: bool = False,
) -> Middleware:
    """Serve files below `root` for GET and HEAD; anything else, and any miss, goes on to the
    next handler (so a route or the 404 answers), as Hono's `serveStatic` does.

    `path` serves that one file whatever the request path; `rewrite_request_path` maps the
    request path first (`lambda p: p.removeprefix("/static")`). A directory answers with its
    `index` file. `mimes` maps an extension without the dot (`"txt"`) to a content type, ahead
    of `mimetypes`. `on_found(path, c)` / `on_not_found(path, c)` run with the file path; the
    first may set headers (`c.header`). `cache_control` is sent only when given.
    `precompressed` serves `file.br`, `file.zst` or `file.gz` when the client accepts that
    encoding and the file exists. Traversal (`../`, `%2e%2e`, absolute paths) never leaves `root`.
    """
    extra = {k.lstrip(".").lower(): v for k, v in (mimes or {}).items()}

    async def run(hook: Hook | None, target: str, c: Context) -> None:
        if hook is not None:
            result = hook(target, c)
            if inspect.isawaitable(result):
                await result

    async def middleware(c: Context, call_next: Next) -> Response:
        if c.req.method not in ("GET", "HEAD"):
            return await call_next()
        requested = path if path is not None else c.req.path
        if rewrite_request_path is not None and path is None:
            requested = rewrite_request_path(requested)
        rel = requested.lstrip("/")
        if "\0" in rel:
            return await call_next()
        if not rel or rel.endswith("/") or (Path(root) / rel).is_dir():
            rel = str(Path(rel) / index)
        ext = Path(rel).suffix.lstrip(".").lower()
        kind = extra.get(ext)
        try:
            response = _open(c, root, rel, kind, precompressed)
        except HTTPError as exc:
            if exc.status != 404:
                raise
            await run(on_not_found, rel, c)
            return await call_next()
        await run(on_found, rel, c)
        if cache_control is not None:
            response.headers.append(("cache-control", cache_control))
        return response

    return middleware


def _open(
    c: Context, root: str | os.PathLike[str], rel: str, kind: str | None, precompressed: bool
) -> Response:
    if precompressed:
        accepted = {
            part.split(";")[0].strip()
            for part in (c.req.header("accept-encoding") or "").split(",")
        }
        for encoding, suffix in _ENCODINGS:
            if encoding not in accepted:
                continue
            try:
                original = c.file(rel, root=root, content_type=kind)  # the real type, not .br's
                response = c.file(rel + suffix, root=root, content_type=_type_of(original))
            except HTTPError:
                continue
            response.headers.append(("content-encoding", encoding))
            response.headers.append(("vary", "Accept-Encoding"))
            return response
    return c.file(rel, root=root, content_type=kind)


def _type_of(response: Response) -> str:
    return next((v for k, v in response.headers if k == "content-type"), "application/octet-stream")
