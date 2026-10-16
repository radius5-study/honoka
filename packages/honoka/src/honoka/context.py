"""The per-request Context and the typed keys it stores values under."""

import mimetypes
import os
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast
from urllib.parse import quote

from honoka.cookies import Prefix, Priority, SameSite, generate_cookie, sign
from honoka.errors import HTTPError, NotFound
from honoka.json import JsonCodec
from honoka.response import FileResponse, Json, Response, Stream
from honoka.sse import SSEEvent, format_event

if TYPE_CHECKING:
    from honoka.request import Request


class Key[T]:
    """A typed slot on the Context. Both `set` and `get` are checked against `T`."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    if TYPE_CHECKING:
        # Makes T invariant. Without it checkers treat Key[User] as Key[object], and
        # `c.set(USER, 1)` would type-check.
        def _invariant(self, value: T, /) -> T: ...


class Context:
    __slots__ = (
        "_codec",
        "_header_ops",
        "_provided",
        "_status",
        "_store",
        "error",
        "req",
        "route",
        "set_cookies",
    )

    def __init__(self, req: "Request", provided: dict[object, object], codec: JsonCodec) -> None:
        self.req = req
        self.error: Exception | None = None
        self.route: str | None = None
        self._store: dict[object, object] | None = None
        self._provided = provided
        self._codec = codec
        self.set_cookies: list[str] | None = None
        self._status = 200
        self._header_ops: list[tuple[str, str | None, bool]] | None = None

    def set[T](self, key: Key[T], value: T) -> None:
        if self._store is None:
            self._store = {}
        self._store[key] = value

    def get[T](self, key: Key[T]) -> T:
        """The value set on this request, else the app-wide value published with `provide`."""
        store = self._store
        if store is not None and key in store:
            return cast(T, store[key])
        if key in self._provided:
            return cast(T, self._provided[key])
        raise LookupError(f"key {key.name!r} was never set")

    def set_validated(self, target: str, value: object) -> None:
        self.req.set_valid(target, value)

    def status(self, code: int) -> None:
        """The status the next `json`/`text`/`html`/`body` answers with when not given one."""
        self._status = code

    def header(self, name: str, value: str | None, *, append: bool = False) -> None:
        """Set a response header; `None` removes it, `append=True` adds another value instead of
        replacing. Applied to whatever response the request ends up with."""
        if self._header_ops is None:
            self._header_ops = []
        self._header_ops.append((name.lower(), value, append))

    def apply_headers(self, response: Response) -> None:
        """Merge what `header()` recorded into `response` (called once, by the app)."""
        for name, value, append in self._header_ops or ():
            if not append:
                response.headers[:] = [h for h in response.headers if h[0].lower() != name]
            if value is not None:
                response.headers.append((name, value))

    def json[T](
        self, obj: T, status: int | None = None, headers: Mapping[str, str] | None = None
    ) -> Json[T]:
        return Json(
            obj,
            self._status if status is None else status,
            None if headers is None else list(headers.items()),
            codec=self._codec,
        )

    def text(
        self, body: str, status: int | None = None, headers: Mapping[str, str] | None = None
    ) -> Response:
        return self.body(body, status, headers, "text/plain; charset=utf-8")

    def html(
        self, body: str, status: int | None = None, headers: Mapping[str, str] | None = None
    ) -> Response:
        return self.body(body, status, headers, "text/html; charset=utf-8")

    def body(
        self,
        data: str | bytes,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
        content_type: str | None = None,
    ) -> Response:
        """A raw response body. `text` and `html` are this with a content type."""
        out = [] if content_type is None else [("content-type", content_type)]
        if headers is not None:
            out.extend(headers.items())
        return Response(
            data.encode() if isinstance(data, str) else data,
            self._status if status is None else status,
            out,
        )

    def not_found(self) -> NoReturn:
        """Answer 404 through the app's `not_found` registrations."""
        raise NotFound

    def file(
        self,
        path: str | os.PathLike[str],
        *,
        root: str | os.PathLike[str] | None = None,
        content_type: str | None = None,
        filename: str | None = None,
        status: int = 200,
    ) -> FileResponse:
        """Send a file. The server streams it from disk; nothing is read into Python.

        When `path` comes from the request, pass `root`: the file must then sit below that
        directory, so `../` and absolute paths answer 404 instead of leaking other files.
        `filename` makes the browser download it under that name."""
        target = Path(path)
        if root is not None:
            base = Path(root).resolve()
            target = (base / target).resolve()
            if not target.is_relative_to(base):
                raise HTTPError(404, "file_not_found")
        if not target.is_file():
            raise HTTPError(404, "file_not_found")
        kind = content_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        headers = [("content-type", kind)]
        if filename is not None:
            headers.append(
                ("content-disposition", f"attachment; filename*=UTF-8''{quote(filename, safe='')}")
            )
        return FileResponse(str(target), status, headers)

    def redirect(self, location: str, status: int = 302) -> Response:
        if "\r" in location or "\n" in location:
            raise ValueError("redirect location must not contain line breaks")
        return Response(b"", status, [("location", location)])

    def stream(
        self,
        chunks: AsyncIterable[str | bytes],
        status: int = 200,
        content_type: str = "application/octet-stream",
    ) -> Stream:
        return Stream(chunks, status, [("content-type", content_type)])

    def cookie(
        self,
        name: str,
        value: str,
        *,
        max_age: int | None = None,
        expires: datetime | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        http_only: bool = True,
        same_site: SameSite | None = "Lax",
        priority: Priority | None = None,
        partitioned: bool = False,
        prefix: Prefix | None = None,
    ) -> None:
        """Queue a Set-Cookie header. Defaults are safe: HttpOnly and SameSite=Lax."""
        header = generate_cookie(
            name,
            value,
            max_age=max_age,
            expires=expires,
            path=path,
            domain=domain,
            secure=secure,
            http_only=http_only,
            same_site=same_site,
            priority=priority,
            partitioned=partitioned,
            prefix=prefix,
        )
        if self.set_cookies is None:
            self.set_cookies = []
        self.set_cookies.append(header)

    def signed_cookie(
        self,
        name: str,
        value: str,
        secret: str,
        *,
        max_age: int | None = None,
        expires: datetime | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        http_only: bool = True,
        same_site: SameSite | None = "Lax",
        priority: Priority | None = None,
        partitioned: bool = False,
        prefix: Prefix | None = None,
    ) -> None:
        """Like `cookie`, with an HMAC-SHA256 signature; read it with `c.req.signed_cookie`."""
        self.cookie(
            name,
            sign(value, secret),
            max_age=max_age,
            expires=expires,
            path=path,
            domain=domain,
            secure=secure,
            http_only=http_only,
            same_site=same_site,
            priority=priority,
            partitioned=partitioned,
            prefix=prefix,
        )

    def delete_cookie(
        self,
        name: str,
        *,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        prefix: Prefix | None = None,
    ) -> str | None:
        """Expire a cookie; returns the value the request had sent for it."""
        sent = self.req.cookie(name, prefix=prefix)
        self.cookie(name, "", max_age=0, path=path, domain=domain, secure=secure, prefix=prefix)
        return sent

    def sse(
        self,
        events: AsyncIterable[str | SSEEvent],
        status: int | None = None,
    ) -> Stream:
        """A Server-Sent Events response. A plain `str` is sent as the data of one event."""

        async def frames() -> AsyncIterator[bytes]:
            async for event in events:
                yield format_event(SSEEvent(event) if isinstance(event, str) else event)

        headers = [
            ("content-type", "text/event-stream"),
            ("cache-control", "no-cache"),
            ("x-accel-buffering", "no"),
        ]
        return Stream(frames(), self._status if status is None else status, headers)

    def stream_text(self, chunks: AsyncIterable[str], status: int | None = None) -> Stream:
        """Stream text chunks (`text/plain`), sent as they are produced."""
        headers = [
            ("content-type", "text/plain; charset=utf-8"),
            ("x-content-type-options", "nosniff"),
        ]
        return Stream(chunks, self._status if status is None else status, headers)
