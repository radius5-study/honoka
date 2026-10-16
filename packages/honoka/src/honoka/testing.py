"""A synchronous client that drives an App through its ASGI entry the way a server does."""

import asyncio
import json as jsonlib
from collections.abc import Coroutine, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Self, cast

if TYPE_CHECKING:
    from honoka.app import App

from honoka.asgi import AsgiMessage


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
        decoded: object = jsonlib.loads(self.body)
        return decoded


async def asgi_call(
    app: "App",
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    content: bytes = b"",
) -> ClientResponse:
    """One request through the app's ASGI entry, checking the ASGI rules the app must keep:
    a single response start before any body, a final body without `more_body`, lowercase
    byte-string headers, and nothing sent afterwards."""
    target, _, query = path.partition("?")
    scope: AsgiMessage = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": target,
        "raw_path": target.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    delivered = False

    async def receive() -> AsgiMessage:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": content, "more_body": False}
        return {"type": "http.disconnect"}

    events: list[AsgiMessage] = []

    async def send(message: AsgiMessage) -> None:
        assert not (events and events[-1].get("more_body") is False), "sent after the final body"
        events.append(message)

    await app(scope, receive, send)
    assert events, "the app returned without sending a response"
    start, *chunks = events
    assert start["type"] == "http.response.start", "the first event must be the response start"
    assert chunks, "a response start needs at least one body event"
    assert all(c["type"] == "http.response.body" for c in chunks), "unexpected event after start"
    assert not chunks[-1].get("more_body"), "the last body event must not set more_body"
    raw = cast(list[tuple[bytes, bytes]], start["headers"])
    assert all(k == k.lower() for k, _ in raw), "ASGI header names must be lowercase"
    return ClientResponse(
        cast(int, start["status"]),
        [(k.decode("latin-1"), v.decode()) for k, v in raw],
        b"".join(cast(bytes, c.get("body", b"")) for c in chunks),
    )


class Client:
    def __init__(self, app: "App") -> None:
        self._app = app
        self._runner: asyncio.Runner | None = None
        self._started = False

    def __enter__(self) -> Self:
        try:
            self._run(self._app.start())
            self._started = True
        except BaseException:
            self.close()
            raise
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
            if self._started:
                self._started = False
                self._runner.run(self._app.stop())
            self._runner.close()
            self._runner = None

    def _run[T](self, coro: Coroutine[object, object, T]) -> T:
        if self._runner is None:
            self._runner = asyncio.Runner()
        return self._runner.run(coro)

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object = None,
        content: bytes | None = None,
    ) -> ClientResponse:
        raw_headers = [(k.lower(), v) for k, v in (headers or {}).items()]
        body = content or b""
        if json is not None:
            body = jsonlib.dumps(json).encode()
            raw_headers.append(("content-type", "application/json"))
        return self._run(
            asgi_call(self._app, method, path, headers=dict(raw_headers), content=body)
        )

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
