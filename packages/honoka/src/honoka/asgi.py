"""The slice of the ASGI interface that honoka relies on, and the lifespan protocol."""

from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import NotRequired, Protocol, TypedDict

type AsgiMessage = MutableMapping[str, object]
type AsgiScope = MutableMapping[str, object]
type Receive = Callable[[], Awaitable[AsgiMessage]]
type Send = Callable[[AsgiMessage], Awaitable[None]]


class HttpScope(TypedDict):
    """The fields of an ASGI `http` scope that the core reads."""

    method: str
    path: str
    query_string: bytes
    headers: Iterable[tuple[bytes, bytes]]
    client: NotRequired[tuple[str, int] | None]
    scheme: NotRequired[str]
    root_path: NotRequired[str]
    server: NotRequired[tuple[str, int | None] | None]


class Lifecycle(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


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
