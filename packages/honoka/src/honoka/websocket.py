"""WebSocket support over ASGI: one handler per route, written against a small `WebSocket`."""

import json
from collections.abc import AsyncIterator, Mapping
from typing import cast

from honoka.asgi import AsgiMessage, Receive, Send


class WebSocketDisconnect(Exception):
    """The peer went away (or closed) while the handler was waiting for a message."""

    def __init__(self, code: int = 1005) -> None:
        super().__init__(f"websocket closed with code {code}")
        self.code = code


class WebSocket:
    """One accepted-or-pending connection. Call `accept()` first, then receive and send."""

    def __init__(
        self,
        scope: Mapping[str, object],
        receive: Receive,
        send: Send,
        params: Mapping[str, str],
        path: str,
    ) -> None:
        self._receive = receive
        self._send = send
        self.params = params
        self.path = path
        self.subprotocols: list[str] = list(cast(list[str], scope.get("subprotocols", ())))
        self._headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in cast(list[tuple[bytes, bytes]], scope.get("headers", ()))
        }
        self._query = cast(bytes, scope.get("query_string", b"")).decode("latin-1")
        self._connected = False
        self._closed = False

    def header(self, name: str) -> str | None:
        return self._headers.get(name.lower())

    @property
    def query_string(self) -> str:
        return self._query

    async def accept(
        self, subprotocol: str | None = None, headers: Mapping[str, str] | None = None
    ) -> None:
        await self._await_connect()
        message: AsgiMessage = {"type": "websocket.accept"}
        if subprotocol is not None:
            message["subprotocol"] = subprotocol
        if headers:
            message["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        await self._send(message)

    async def _await_connect(self) -> None:
        if not self._connected:
            self._connected = True
            first = await self._receive()
            if first["type"] != "websocket.connect":
                raise WebSocketDisconnect

    async def _next(self) -> AsgiMessage:
        message = await self._receive()
        if message["type"] == "websocket.disconnect":
            self._closed = True
            raise WebSocketDisconnect(cast(int, message.get("code", 1005)))
        return message

    async def receive_text(self) -> str:
        message = await self._next()
        text = message.get("text")
        if text is None:
            return cast(bytes, message.get("bytes", b"")).decode()
        return cast(str, text)

    async def receive_bytes(self) -> bytes:
        message = await self._next()
        data = message.get("bytes")
        if data is None:
            return cast(str, message.get("text", "")).encode()
        return cast(bytes, data)

    async def receive_json(self) -> object:
        decoded: object = json.loads(await self.receive_text())
        return decoded

    async def send_text(self, data: str) -> None:
        await self._send({"type": "websocket.send", "text": data})

    async def send_bytes(self, data: bytes) -> None:
        await self._send({"type": "websocket.send", "bytes": data})

    async def send_json(self, data: object) -> None:
        await self.send_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if not self._closed:
            self._closed = True
            await self._send({"type": "websocket.close", "code": code, "reason": reason})

    async def __aiter__(self) -> AsyncIterator[str]:
        """Text messages until the peer disconnects."""
        while True:
            try:
                yield await self.receive_text()
            except WebSocketDisconnect:
                return
