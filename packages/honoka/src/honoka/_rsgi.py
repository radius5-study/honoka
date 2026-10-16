"""The slice of Granian's RSGI interface that honoka relies on, as structural types."""

from collections.abc import Iterable
from typing import Protocol


class Headers(Protocol):
    def get(self, key: str, default: str | None = None, /) -> str | None: ...

    def items(self) -> Iterable[tuple[str, str]]: ...


class Scope(Protocol):
    @property
    def method(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def query_string(self) -> str: ...

    @property
    def headers(self) -> Headers: ...

    @property
    def client(self) -> str: ...


class Proto(Protocol):
    async def __call__(self) -> bytes: ...

    def response_empty(self, status: int, headers: list[tuple[str, str]], /) -> None: ...

    def response_bytes(
        self, status: int, headers: list[tuple[str, str]], body: bytes, /
    ) -> None: ...
