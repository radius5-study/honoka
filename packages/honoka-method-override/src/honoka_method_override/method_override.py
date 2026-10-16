from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

from honoka import App
from honoka.asgi import AsgiMessage, AsgiScope, Receive, Send

_FORM = "application/x-www-form-urlencoded"
_METHODS = frozenset({"GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "POST"})


def method_override(
    app: App,
    *,
    form: str = "_method",
    header: str | None = None,
    query: str | None = None,
) -> Callable[[AsgiScope, Receive, Send], Awaitable[None]]:
    """Let a POST be routed as another method (`<form method=post>` carrying `_method=DELETE`).

    Unlike Hono's `app.use()` middleware this is an ASGI wrapper: honoka routes on the ASGI
    scope's method before any middleware runs, so the method has to change ahead of the app.
    Serve the result instead of `app` (`serve(method_override(app))`, or
    `asgi_call(method_override(app), ...)` in tests; `app.test()` takes only an App).

    Only POST requests are touched. The value comes from the `header` if that option is set,
    else from the `query` parameter if set, else from the urlencoded body field `form`. A value
    that is not an HTTP method is ignored. When the body is read it is buffered (urlencoded
    forms are small) and replayed to the app, so handlers still see it."""

    async def wrapper(scope: AsgiScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await app(scope, receive, send)
            return
        headers = _headers(scope)
        value: str | None
        if header is not None:
            value = headers.get(header.lower())
        elif query is not None:
            raw = scope.get("query_string", b"")
            qs = parse_qs(raw.decode("latin-1") if isinstance(raw, bytes) else "")
            value = (qs.get(query) or [None])[0]
        elif headers.get("content-type", "").split(";")[0].strip().lower() == _FORM:
            messages = await _buffer(receive)
            body = b"".join(_chunk(m) for m in messages)
            value = (parse_qs(body.decode("utf-8", "replace")).get(form) or [None])[0]
            receive = _replay(messages)
        else:
            value = None
        method = value.strip().upper() if value else ""
        if method in _METHODS:
            scope = {**scope, "method": method}
        await app(scope, receive, send)

    return wrapper


def _headers(scope: AsgiScope) -> dict[str, str]:
    raw = scope.get("headers", [])
    out: dict[str, str] = {}
    if isinstance(raw, list):
        for pair in raw:
            if isinstance(pair, tuple) and len(pair) == 2:
                k, v = pair
                if isinstance(k, bytes) and isinstance(v, bytes):
                    out.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
    return out


def _chunk(message: AsgiMessage) -> bytes:
    body = message.get("body", b"")
    return body if isinstance(body, bytes) else b""


async def _buffer(receive: Receive) -> list[AsgiMessage]:
    messages: list[AsgiMessage] = []
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request" or not message.get("more_body"):
            return messages


def _replay(messages: list[AsgiMessage]) -> Receive:
    pending = list(messages)

    async def receive() -> AsgiMessage:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    return receive
