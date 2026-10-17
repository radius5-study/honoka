"""Helpers the tests share."""

import asyncio
import contextlib
import json
from collections.abc import Callable

from honoka import App
from honoka.asgi import AsgiMessage, AsgiScope
from honoka.testing import ClientResponse, asgi_call

SECRET = "test-secret-" + "x" * 32

type SignIn = Callable[[str], dict[str, str]]


def body(resp: ClientResponse) -> dict[str, object]:
    """The response body, which these tests expect to be a JSON object."""
    data = resp.json()
    assert isinstance(data, dict), data
    return {str(key): value for key, value in data.items()}


def rows(resp: ClientResponse) -> list[dict[str, object]]:
    """The response body, which these tests expect to be a JSON array of objects."""
    data = resp.json()
    assert isinstance(data, list), data
    return [{str(k): v for k, v in item.items()} for item in data if isinstance(item, dict)]


async def sign_up(app: App) -> tuple[str, dict[str, str]]:
    """Register ann, log in and create project 1 through the ASGI entry. Returns her token and the
    headers for further requests."""
    json_type = {"content-type": "application/json"}
    payload = json.dumps({"email": "ann@example.com", "password": "correct horse battery"}).encode()
    await asgi_call(app, "POST", "/auth/register", headers=json_type, content=payload)
    login = await asgi_call(app, "POST", "/auth/login", headers=json_type, content=payload)
    token = json.loads(login.text)["access_token"]
    auth = {"authorization": f"Bearer {token}", **json_type}
    await asgi_call(app, "POST", "/projects", headers=auth, content=b'{"name": "P"}')
    return token, auth


async def open_stream(app: App, token: str, received: bytearray) -> asyncio.Task[None]:
    """Start `GET /projects/1/events` as a client that never hangs up, collecting what the server
    sends in `received`. Cancel the task to hang up."""
    asked = False
    hang_up = asyncio.Event()

    async def receive() -> AsgiMessage:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await hang_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: AsgiMessage) -> None:
        chunk = message.get("body")
        if isinstance(chunk, bytes):
            received.extend(chunk)

    scope: AsgiScope = {
        "type": "http",
        "method": "GET",
        "path": "/projects/1/events",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(0.05)  # let it subscribe
    return task


async def ends_within(task: asyncio.Task[None], seconds: float) -> bool:
    """Whether the stream finished by itself in time; if not it is hung up on."""
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return bool(done)
