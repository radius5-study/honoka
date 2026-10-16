"""method_override: an ASGI wrapper, so the tests drive the ASGI callable directly."""

import asyncio
from collections.abc import Awaitable, Callable

from honoka_method_override import method_override

from honoka import App, Context, Response
from honoka.asgi import AsgiMessage, AsgiScope, Receive, Send

FORM = {"content-type": "application/x-www-form-urlencoded"}

type AsgiApp = Callable[[AsgiScope, Receive, Send], Awaitable[None]]


def build() -> App:
    app = App()

    @app.post("/posts/1")
    async def create(c: Context) -> Response:
        return c.text("post:" + (await c.req.body()).decode())

    @app.delete("/posts/1")
    async def delete(c: Context) -> Response:
        return c.text("delete:" + (await c.req.body()).decode())

    @app.put("/posts/1")
    def put(c: Context) -> Response:
        return c.text("put")

    return app


def call(
    app: AsgiApp,
    method: str,
    target: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> tuple[int, str]:
    path, _, query = target.partition("?")
    scope: AsgiScope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    sent = False

    async def receive() -> AsgiMessage:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    events: list[AsgiMessage] = []

    async def send(message: AsgiMessage) -> None:
        events.append(message)

    async def run() -> None:
        await app(scope, receive, send)

    asyncio.run(run())
    status = events[0]["status"]
    assert isinstance(status, int)
    text = b""
    for event in events[1:]:
        chunk = event.get("body")
        if isinstance(chunk, bytes):
            text += chunk
    return status, text.decode()


def test_form_field_overrides_and_body_is_replayed() -> None:
    app = method_override(build())
    assert call(app, "POST", "/posts/1", FORM, b"_method=delete&x=1") == (
        200,
        "delete:_method=delete&x=1",
    )


def test_custom_form_field_and_unknown_values() -> None:
    app = method_override(build(), form="m")
    assert call(app, "POST", "/posts/1", FORM, b"m=PUT") == (200, "put")
    assert call(app, "POST", "/posts/1", FORM, b"_method=PUT") == (200, "post:_method=PUT")
    assert call(app, "POST", "/posts/1", FORM, b"m=BREW")[1] == "post:m=BREW"
    assert call(app, "POST", "/posts/1", None, b"m=PUT")[1] == "post:m=PUT"


def test_header_and_query_sources() -> None:
    by_header = method_override(build(), header="X-HTTP-Method-Override")
    assert call(by_header, "POST", "/posts/1", {"x-http-method-override": "DELETE"})[1] == "delete:"
    assert call(by_header, "POST", "/posts/1", FORM, b"_method=DELETE")[1].startswith("post:")
    by_query = method_override(build(), query="_method")
    assert call(by_query, "POST", "/posts/1?_method=put") == (200, "put")
    assert call(by_query, "POST", "/posts/1")[1] == "post:"


def test_only_post_is_overridden() -> None:
    app = method_override(build())
    assert call(app, "GET", "/posts/1?_method=DELETE")[0] == 405
    assert call(app, "PUT", "/posts/1", FORM, b"_method=DELETE") == (200, "put")


def test_only_put_patch_and_delete_can_be_the_override() -> None:
    """A cross-site form that says `_method=GET` must not turn into a "safe" request."""
    app = App()

    @app.on(["GET", "POST"], "/x")
    def x(c: Context) -> Response:
        return c.text(c.req.method)

    wrapped = method_override(app)
    for forced in ("GET", "HEAD", "OPTIONS", "CONNECT", "TRACE"):
        got = call(wrapped, "POST", "/x", FORM, f"_method={forced}".encode())
        assert got == (200, "POST"), forced


def test_an_oversized_form_is_not_read_to_the_end_and_still_reaches_the_app_whole() -> None:
    chunks = 100
    messages: list[AsgiMessage] = [
        {"type": "http.request", "body": b"x" * 1024, "more_body": True} for _ in range(chunks)
    ]
    messages.append({"type": "http.request", "body": b"", "more_body": False})
    taken = 0
    consumed_when_the_app_started: list[int] = []
    received: list[int] = []

    async def receive() -> AsgiMessage:
        nonlocal taken
        taken += 1
        return messages[taken - 1]

    async def send(message: AsgiMessage) -> None: ...

    app = App()

    @app.post("/upload")
    async def upload(c: Context) -> Response:
        consumed_when_the_app_started.append(taken)
        received.append(len(await c.req.body()))
        return c.text("ok")

    scope: AsgiScope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "query_string": b"",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
    }
    wrapper = method_override(app, max_body=4096)

    async def run() -> None:
        await wrapper(scope, receive, send)

    asyncio.run(run())
    assert consumed_when_the_app_started[0] < chunks // 2  # it did not buffer the whole stream
    assert received == [chunks * 1024]  # yet the app still got every byte
