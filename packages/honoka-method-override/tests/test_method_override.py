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
