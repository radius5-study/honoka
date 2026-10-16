"""A synchronous client that drives an App through its ASGI entry the way a server does."""

import asyncio
import json as jsonlib
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Self, cast
from urllib.parse import urlencode

if TYPE_CHECKING:
    from honoka.app import App

type AsgiApp = Callable[[AsgiScope, Receive, Send], Awaitable[None]]

from honoka.asgi import AsgiMessage, AsgiScope, Receive, Send


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
    app: AsgiApp,
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
        form: Mapping[str, str] | None = None,
    ) -> ClientResponse:
        raw_headers = [(k.lower(), v) for k, v in (headers or {}).items()]
        body = content or b""
        if json is not None:
            body = jsonlib.dumps(json).encode()
            raw_headers.append(("content-type", "application/json"))
        if form is not None:
            body = urlencode(form).encode()
            raw_headers.append(("content-type", "application/x-www-form-urlencoded"))
        return self._run(
            asgi_call(self._app, method, path, headers=dict(raw_headers), content=body)
        )

    def get(self, path: str, *, headers: Mapping[str, str] | None = None) -> ClientResponse:
        return self.request("GET", path, headers=headers)

    def head(self, path: str, *, headers: Mapping[str, str] | None = None) -> ClientResponse:
        return self.request("HEAD", path, headers=headers)

    def options(self, path: str, *, headers: Mapping[str, str] | None = None) -> ClientResponse:
        return self.request("OPTIONS", path, headers=headers)

    def delete(self, path: str, *, headers: Mapping[str, str] | None = None) -> ClientResponse:
        return self.request("DELETE", path, headers=headers)

    def post(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object = None,
        content: bytes | None = None,
        form: Mapping[str, str] | None = None,
    ) -> ClientResponse:
        return self.request("POST", path, headers=headers, json=json, content=content, form=form)

    def put(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object = None,
        content: bytes | None = None,
    ) -> ClientResponse:
        return self.request("PUT", path, headers=headers, json=json, content=content)

    def patch(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object = None,
        content: bytes | None = None,
    ) -> ClientResponse:
        return self.request("PATCH", path, headers=headers, json=json, content=content)

    def websocket(self, path: str, *, headers: Mapping[str, str] | None = None) -> "WebSocketTest":
        """Open a WebSocket to the app: `with client.websocket("/chat") as ws: ws.send_text("hi")`."""
        return WebSocketTest(self, path, headers or {})


class WebSocketTest:
    """The client end of a WebSocket, driven in process on the Client's event loop."""

    def __init__(self, client: Client, path: str, headers: Mapping[str, str]) -> None:
        target, _, query = path.partition("?")
        self._client = client
        self._to_app: asyncio.Queue[AsgiMessage] = asyncio.Queue()
        self._from_app: asyncio.Queue[AsgiMessage] = asyncio.Queue()
        self._scope: AsgiMessage = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": target,
            "query_string": query.encode(),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "subprotocols": [],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        self._task: asyncio.Task[None] | None = None
        self.accepted = False
        self.close_code: int | None = None

    def __enter__(self) -> Self:
        async def start() -> None:
            self._task = asyncio.create_task(
                self._client._app(self._scope, self._to_app.get, self._from_app.put)
            )
            await self._to_app.put({"type": "websocket.connect"})

        self._client._run(start())
        first = self._client._run(self._from_app.get())
        if first["type"] == "websocket.accept":
            self.accepted = True
        else:
            self.close_code = cast(int, first.get("code", 1000))
            self._client._run(self._finish())
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    async def _finish(self) -> None:
        if self._task is not None:
            await self._task
            self._task = None

    def send_text(self, data: str) -> None:
        self._client._run(self._to_app.put({"type": "websocket.receive", "text": data}))

    def send_bytes(self, data: bytes) -> None:
        self._client._run(self._to_app.put({"type": "websocket.receive", "bytes": data}))

    def send_json(self, data: object) -> None:
        self.send_text(jsonlib.dumps(data))

    def _receive(self) -> AsgiMessage:
        message = self._client._run(self._from_app.get())
        if message["type"] == "websocket.close":
            self.close_code = cast(int, message.get("code", 1000))
            self._client._run(self._finish())
            raise AssertionError(f"the server closed the websocket with code {self.close_code}")
        return message

    def receive_text(self) -> str:
        return cast(str, self._receive()["text"])

    def receive_bytes(self) -> bytes:
        return cast(bytes, self._receive()["bytes"])

    def receive_json(self) -> object:
        decoded: object = jsonlib.loads(self.receive_text())
        return decoded

    def close(self, code: int = 1000) -> None:
        if self._task is not None:
            self._client._run(self._to_app.put({"type": "websocket.disconnect", "code": code}))
            self._client._run(self._finish())
