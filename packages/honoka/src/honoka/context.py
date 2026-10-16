"""The per-request Context and the typed keys it stores values under."""

import re
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING, Literal, cast

from honoka.json import JsonCodec
from honoka.response import Json, Response, Stream

if TYPE_CHECKING:
    from honoka.request import Request


_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_COOKIE_ATTR = re.compile(r"[\x21\x23-\x3a\x3c-\x7e]+")
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")


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
    __slots__ = ("_codec", "_provided", "_store", "error", "req", "route", "set_cookies")

    def __init__(self, req: "Request", provided: dict[object, object], codec: JsonCodec) -> None:
        self.req = req
        self.error: Exception | None = None
        self.route: str | None = None
        self._store: dict[object, object] = {}
        self._provided = provided
        self._codec = codec
        self.set_cookies: list[str] = []

    def set[T](self, key: Key[T], value: T) -> None:
        self._store[key] = value

    def get[T](self, key: Key[T]) -> T:
        """The value set on this request, else the app-wide value published with `provide`."""
        if key in self._store:
            return cast(T, self._store[key])
        if key in self._provided:
            return cast(T, self._provided[key])
        raise LookupError(f"key {key.name!r} was never set")

    def set_validated(self, target: str, value: object) -> None:
        self.req.set_valid(target, value)

    def json[T](self, obj: T, status: int = 200) -> Json[T]:
        return Json(obj, status, codec=self._codec)

    def text(self, body: str, status: int = 200) -> Response:
        return Response(body.encode(), status, [("content-type", "text/plain; charset=utf-8")])

    def html(self, body: str, status: int = 200) -> Response:
        return Response(body.encode(), status, [("content-type", "text/html; charset=utf-8")])

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
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        http_only: bool = True,
        same_site: Literal["Lax", "Strict", "None"] | None = "Lax",
    ) -> None:
        """Queue a Set-Cookie header. Defaults are safe: HttpOnly and SameSite=Lax."""
        if not _COOKIE_NAME.fullmatch(name) or not _COOKIE_VALUE.fullmatch(value):
            raise ValueError("cookie name or value contains characters that are not allowed")
        if not _COOKIE_ATTR.fullmatch(path) or (
            domain is not None and not _COOKIE_ATTR.fullmatch(domain)
        ):
            raise ValueError("cookie path or domain contains characters that are not allowed")
        parts = [f"{name}={value}", f"Path={path}"]
        if domain is not None:
            parts.append(f"Domain={domain}")
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if secure or same_site == "None":
            parts.append("Secure")
        if http_only:
            parts.append("HttpOnly")
        if same_site is not None:
            parts.append(f"SameSite={same_site}")
        self.set_cookies.append("; ".join(parts))

    def delete_cookie(self, name: str, *, path: str = "/", domain: str | None = None) -> None:
        self.cookie(name, "", max_age=0, path=path, domain=domain)
