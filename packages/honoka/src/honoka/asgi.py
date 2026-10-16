"""ASGI entry for an App: ASGI's scope/receive/send, adapted to the RSGI-shaped `Scope` and `Proto`
the core speaks, so one app runs on Granian (RSGI), uvicorn, Cloudflare Python Workers and any
other ASGI host with the same `Context` and the same handlers.

RSGI answers by calling a synchronous method; ASGI answers by awaiting `send`. The adapter records
what the core answered and sends it once the core returns. Streams are the exception: their chunks
go out as they are produced."""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, MutableMapping
from pathlib import Path
from typing import Protocol, cast

type AsgiMessage = MutableMapping[str, object]
type AsgiScope = MutableMapping[str, object]
type Receive = Callable[[], Awaitable[AsgiMessage]]
type Send = Callable[[AsgiMessage], Awaitable[None]]


class Lifecycle(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class AsgiHeaders:
    __slots__ = ("_map", "_raw")

    def __init__(self, raw: Iterable[tuple[bytes, bytes]]) -> None:
        self._raw = raw
        self._map: dict[str, str] | None = None

    def _lookup(self) -> dict[str, str]:
        if self._map is None:
            table: dict[str, str] = {}
            for name, value in self._raw:
                key = name.decode("latin-1").lower()
                text = value.decode("latin-1")
                # HTTP/2 may split `cookie` into several fields; RFC 9110 joins the rest with ", ".
                if key in table:
                    text = table[key] + ("; " if key == "cookie" else ", ") + text
                table[key] = text
            self._map = table
        return self._map

    def get(self, key: str, default: str | None = None, /) -> str | None:
        return self._lookup().get(key, default)

    def items(self) -> Iterable[tuple[str, str]]:
        return list(self._lookup().items())


class HttpScope:
    """The part of an ASGI `http` scope that the core reads, shaped like an RSGI scope."""

    __slots__ = ("client", "headers", "method", "path", "query_string")

    def __init__(self, scope: AsgiScope) -> None:
        self.method = str(scope["method"])
        self.path = str(scope["path"])
        self.query_string = cast(bytes, scope.get("query_string", b"")).decode("latin-1")
        client = scope.get("client")
        self.client = f"{client[0]}:{client[1]}" if isinstance(client, (tuple, list)) else ""
        self.headers = AsgiHeaders(cast(Iterable[tuple[bytes, bytes]], scope.get("headers", ())))


def _encode(headers: list[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    # UTF-8, as Granian's RSGI sends str headers: a redirect to "/日本語" must not crash the response.
    return [(name.lower().encode("latin-1"), value.encode()) for name, value in headers]


class _Transport:
    __slots__ = ("_proto",)

    def __init__(self, proto: "AsgiProto") -> None:
        self._proto = proto

    async def send_bytes(self, data: bytes, /) -> None:
        await self._proto.begin_stream()
        await self._proto.send({"type": "http.response.body", "body": data, "more_body": True})


class AsgiProto:
    __slots__ = ("_pending", "_receive", "_started", "_stream", "send")

    def __init__(self, receive: Receive, send: Send) -> None:
        self._receive = receive
        self.send = send
        self._pending: tuple[int, list[tuple[str, str]], bytes] | None = None
        self._stream: tuple[int, list[tuple[str, str]]] | None = None
        self._started = False

    # -- request side -------------------------------------------------------------------------

    async def __call__(self) -> bytes:
        body = bytearray()
        async for chunk in self:
            body += chunk
        return bytes(body)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            message = await self._receive()
            if message["type"] != "http.request":  # http.disconnect
                return
            chunk = cast(bytes, message.get("body", b""))
            if chunk:
                yield chunk
            if not message.get("more_body"):
                return

    # -- response side: the core calls these synchronously -------------------------------------

    def response_empty(self, status: int, headers: list[tuple[str, str]], /) -> None:
        self._pending = (status, headers, b"")

    def response_bytes(self, status: int, headers: list[tuple[str, str]], body: bytes, /) -> None:
        self._pending = (status, headers, body)

    def response_file(self, status: int, headers: list[tuple[str, str]], file: str, /) -> None:
        # ASGI has no portable "send this file"; read it (hosts such as Workers have no disk anyway).
        self._pending = (status, headers, Path(file).read_bytes())

    def response_stream(self, status: int, headers: list[tuple[str, str]], /) -> _Transport:
        self._stream = (status, headers)
        return _Transport(self)

    # -- sending ------------------------------------------------------------------------------

    async def begin_stream(self) -> None:
        if not self._started and self._stream is not None:
            self._started = True
            status, headers = self._stream
            await self.send(
                {"type": "http.response.start", "status": status, "headers": _encode(headers)}
            )

    async def finish(self) -> None:
        """Send what the core answered. Must run exactly once, after the core returned."""
        if self._stream is not None:
            await self.begin_stream()
            await self.send({"type": "http.response.body", "body": b"", "more_body": False})
        elif self._pending is not None:
            status, headers, body = self._pending
            await self.send(
                {"type": "http.response.start", "status": status, "headers": _encode(headers)}
            )
            await self.send({"type": "http.response.body", "body": body})


async def run_lifespan(app: Lifecycle, receive: Receive, send: Send) -> None:
    """The ASGI lifespan protocol: run startup and shutdown hooks and report failures."""
    while True:
        message = await receive()
        kind = str(message["type"])
        if kind == "lifespan.startup":
            try:
                await app.start()
            except Exception as exc:  # noqa: BLE001 - the server is told, it decides what to do
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
            await send({"type": "lifespan.startup.complete"})
        elif kind == "lifespan.shutdown":
            try:
                await app.stop()
            except Exception as exc:  # noqa: BLE001 - as above
                await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
                return
            await send({"type": "lifespan.shutdown.complete"})
            return
