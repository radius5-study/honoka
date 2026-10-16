"""A synchronous client that drives an App through its real RSGI entry with a fake server."""

import asyncio
from collections.abc import Iterable, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Self

import msgspec

if TYPE_CHECKING:
    from honoka.app import App


class ClientResponse:
    __slots__ = ("body", "headers", "raw_headers", "status")

    def __init__(self, status: int, raw_headers: list[tuple[str, str]], body: bytes) -> None:
        self.status = status
        self.raw_headers = raw_headers
        self.headers = {k.lower(): v for k, v in raw_headers}
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self) -> object:
        return msgspec.json.decode(self.body)


class _Headers:
    def __init__(self, items: list[tuple[str, str]]) -> None:
        self._items = items

    def get(self, key: str, default: str | None = None, /) -> str | None:
        for k, v in self._items:
            if k == key:
                return v
        return default

    def items(self) -> Iterable[tuple[str, str]]:
        return list(self._items)


class _Scope:
    def __init__(
        self, method: str, path: str, query_string: str, headers: list[tuple[str, str]]
    ) -> None:
        self.method = method
        self.path = path
        self.query_string = query_string
        self.headers = _Headers(headers)
        self.client = "127.0.0.1:50000"


class _Proto:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.response: ClientResponse | None = None

    async def __call__(self) -> bytes:
        return self._body

    def response_empty(self, status: int, headers: list[tuple[str, str]], /) -> None:
        self.response = ClientResponse(status, headers, b"")

    def response_bytes(self, status: int, headers: list[tuple[str, str]], body: bytes, /) -> None:
        self.response = ClientResponse(status, headers, body)


class Client:
    def __init__(self, app: "App") -> None:
        self._app = app
        self._runner: asyncio.Runner | None = None

    def __enter__(self) -> Self:
        self._app.finalize()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._runner is not None:
            self._runner.close()
            self._runner = None

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object = None,
        content: bytes | None = None,
    ) -> ClientResponse:
        target, _, query = path.partition("?")
        raw_headers = [(k.lower(), v) for k, v in (headers or {}).items()]
        body = content or b""
        if json is not None:
            body = msgspec.json.encode(json)
            raw_headers.append(("content-type", "application/json"))
        proto = _Proto(body)
        if self._runner is None:
            self._runner = asyncio.Runner()
        self._runner.run(self._app.__rsgi__(_Scope(method, target, query, raw_headers), proto))
        if proto.response is None:
            raise AssertionError("the app returned without sending a response")
        return proto.response

    def get(self, path: str, *, headers: Mapping[str, str] | None = None) -> ClientResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object = None,
        content: bytes | None = None,
    ) -> ClientResponse:
        return self.request("POST", path, headers=headers, json=json, content=content)
