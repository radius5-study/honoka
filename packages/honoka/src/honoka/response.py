"""Responses. `Json[T]` keeps the original object so the body type survives into annotations."""

import msgspec


class Response:
    __slots__ = ("body", "headers", "status")

    def __init__(
        self,
        body: bytes = b"",
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers: list[tuple[str, str]] = [] if headers is None else headers


class Json[T](Response):
    """A JSON response whose body type is `T`. Structs are encoded directly, without dicts."""

    __slots__ = ("_data",)

    def __init__(
        self, data: T, status: int = 200, headers: list[tuple[str, str]] | None = None
    ) -> None:
        content_type = ("content-type", "application/json")
        super().__init__(msgspec.json.encode(data), status, [*(headers or []), content_type])
        self._data = data

    @property
    def data(self) -> T:
        return self._data
