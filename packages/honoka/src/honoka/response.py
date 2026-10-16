"""Responses. `Json[T]` keeps the original object so the body type survives into annotations."""

from collections.abc import AsyncIterable

from honoka.json import STDLIB_JSON, JsonCodec

_JSON_TYPE = ("content-type", "application/json")


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
    """A JSON response whose body type is `T`, encoded by `codec` (stdlib JSON by default)."""

    __slots__ = ("_data",)

    def __init__(
        self,
        data: T,
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
        *,
        codec: JsonCodec = STDLIB_JSON,
    ) -> None:
        # Assigned directly: Response.__init__ would only add a call to every JSON response.
        self.body = codec.encode(data)
        self.status = status
        self.headers = [_JSON_TYPE] if headers is None else [*headers, _JSON_TYPE]
        self._data = data

    @property
    def data(self) -> T:
        return self._data


class Stream(Response):
    """A response whose body is produced chunk by chunk (`str` chunks are encoded as UTF-8)."""

    __slots__ = ("chunks",)

    def __init__(
        self,
        chunks: AsyncIterable[str | bytes],
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(b"", status, headers)
        self.chunks = chunks
