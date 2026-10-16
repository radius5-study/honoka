"""The per-request Context and the typed keys it stores values under."""

from typing import TYPE_CHECKING, cast

from honoka.response import Json


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
    __slots__ = ("_store",)

    def __init__(self) -> None:
        self._store: dict[object, object] = {}

    def set[T](self, key: Key[T], value: T) -> None:
        self._store[key] = value

    def get[T](self, key: Key[T]) -> T:
        try:
            return cast(T, self._store[key])
        except KeyError:
            raise LookupError(f"key {key.name!r} was never set") from None

    def json[T](self, obj: T, status: int = 200) -> Json[T]:
        return Json(obj, status)
