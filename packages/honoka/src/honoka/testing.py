"""A synchronous client that drives an App through its real RSGI entry with a fake server."""

import asyncio
import json as jsonlib
from collections.abc import AsyncIterator, Coroutine, Iterable, Mapping
from pathlib import Path
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


class _Transport:
    def __init__(self, response: ClientResponse) -> None:
        self._response = response

    async def send_bytes(self, data: bytes, /) -> None:
        self._response.body += data


class _Proto:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.response: ClientResponse | None = None

    async def __call__(self) -> bytes:
        return self._body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self._body), 4):
            yield self._body[start : start + 4]

    def response_empty(self, status: int, headers: list[tuple[str, str]], /) -> None:
        self.response = ClientResponse(status, headers, b"")

    def response_bytes(self, status: int, headers: list[tuple[str, str]], body: bytes, /) -> None:
        self.response = ClientResponse(status, headers, body)

    def response_file(self, status: int, headers: list[tuple[str, str]], file: str, /) -> None:
        self.response = ClientResponse(status, headers, Path(file).read_bytes())

    def response_stream(self, status: int, headers: list[tuple[str, str]], /) -> _Transport:
        self.response = ClientResponse(status, headers, b"")
        return _Transport(self.response)


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
        [(k.decode("latin-1"), v.decode("latin-1")) for k, v in raw],
        b"".join(cast(bytes, c.get("body", b"")) for c in chunks),
    )


class _Lifespan:
    """Drives the app's ASGI lifespan protocol the way an ASGI server does."""

    def __init__(self, app: "App") -> None:
        self._app = app
        self._inbox: asyncio.Queue[AsgiMessage] = asyncio.Queue()
        self._outbox: asyncio.Queue[AsgiMessage] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        scope: AsgiMessage = {"type": "lifespan", "asgi": {"version": "3.0"}}
        self._task = asyncio.create_task(self._app(scope, self._inbox.get, self._outbox.put))
        await self._inbox.put({"type": "lifespan.startup"})
        reply = await self._outbox.get()
        if reply["type"] != "lifespan.startup.complete":
            raise RuntimeError(str(reply.get("message", "startup failed")))

    async def shutdown(self) -> None:
        await self._inbox.put({"type": "lifespan.shutdown"})
        reply = await self._outbox.get()
        if self._task is not None:
            await self._task
        if reply["type"] != "lifespan.shutdown.complete":
            raise RuntimeError(str(reply.get("message", "shutdown failed")))


class Client:
    def __init__(self, app: "App", interface: str = "rsgi") -> None:
        if interface not in ("rsgi", "asgi"):
            raise ValueError(f"interface must be 'rsgi' or 'asgi', not {interface!r}")
        self._app = app
        self._interface = interface
        self._runner: asyncio.Runner | None = None
        self._lifespan = _Lifespan(app) if interface == "asgi" else None
        self._started = False

    def __enter__(self) -> Self:
        try:
            self._run(self._lifespan.startup() if self._lifespan else self._app.start())
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
                self._runner.run(self._lifespan.shutdown() if self._lifespan else self._app.stop())
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
        target, _, query = path.partition("?")
        raw_headers = [(k.lower(), v) for k, v in (headers or {}).items()]
        body = content or b""
        if json is not None:
            body = jsonlib.dumps(json).encode()
            raw_headers.append(("content-type", "application/json"))
        if self._interface == "asgi":
            return self._run(
                asgi_call(self._app, method, path, headers=dict(raw_headers), content=body)
            )
        proto = _Proto(body)
        self._run(self._app.__rsgi__(_Scope(method, target, query, raw_headers), proto))
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
