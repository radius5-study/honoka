"""The per-request Context and the typed keys it stores values under."""

from typing import TYPE_CHECKING, cast

from honoka.response import Json, Response

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
    __slots__ = ("_provided", "_store", "error", "req", "route")

    def __init__(self, req: "Request", provided: dict[object, object]) -> None:
        self.req = req
        self.error: Exception | None = None
        self.route: str | None = None
        self._store: dict[object, object] = {}
        self._provided = provided

    def set[T](self, key: Key[T], value: T) -> None:
        self._store[key] = value

    def get[T](self, key: Key[T]) -> T:
        """The value set on this request, else the app-wide value published with `provide`."""
        if key in self._store:
            return cast(T, self._store[key])
        if key in self._provided:
            return cast(T, self._provided[key])
        raise LookupError(f"key {key.name!r} was never set")

    def json[T](self, obj: T, status: int = 200) -> Json[T]:
        return Json(obj, status)

    def text(self, body: str, status: int = 200) -> Response:
        return Response(body.encode(), status, [("content-type", "text/plain; charset=utf-8")])

    def html(self, body: str, status: int = 200) -> Response:
        return Response(body.encode(), status, [("content-type", "text/html; charset=utf-8")])
