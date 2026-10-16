from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

from honoka import App
from honoka.asgi import AsgiMessage, AsgiScope, Receive, Send

_FORM = "application/x-www-form-urlencoded"
# Only the methods an HTML form cannot send. Turning a cross-site POST into a "safe" GET would
# slip past CSRF checks that trust the method.
_METHODS = frozenset({"PUT", "PATCH", "DELETE"})
_MAX_BODY = 64 * 1024


def method_override(
    app: App,
    *,
    form: str = "_method",
    header: str | None = None,
    query: str | None = None,
    max_body: int = _MAX_BODY,
) -> Callable[[AsgiScope, Receive, Send], Awaitable[None]]:
    """Let a POST be routed as another method (`<form method=post>` carrying `_method=DELETE`).

    Unlike Hono's `app.use()` middleware this is an ASGI wrapper: honoka routes on the ASGI
    scope's method before any middleware runs, so the method has to change ahead of the app.
    Serve the result instead of `app` (`serve(method_override(app))`, or
    `asgi_call(method_override(app), ...)` in tests; `app.test()` takes only an App).

    Only POST requests are touched. The value comes from the `header` if that option is set,
    else from the `query` parameter if set, else from the urlencoded body field `form`. A value
    that is not PUT, PATCH or DELETE is ignored. The form is read only up to `max_body` bytes;
    what was read is replayed to the app, followed by the rest of the stream, so handlers and
    `body_limit` still see the whole body."""

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
            messages, complete = await _buffer(receive, max_body)
            body = b"".join(_chunk(m) for m in messages)
            value = (
                (parse_qs(body.decode("utf-8", "replace")).get(form) or [None])[0]
                if complete
                else None
            )
            receive = _replay(messages, receive)
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


async def _buffer(receive: Receive, limit: int) -> tuple[list[AsgiMessage], bool]:
    """Read the body up to `limit` bytes. The flag says whether all of it was read."""
    messages: list[AsgiMessage] = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request" or not message.get("more_body"):
            return messages, size + len(_chunk(message)) <= limit
        size += len(_chunk(message))
        if size > limit:
            return messages, False


def _replay(messages: list[AsgiMessage], rest: Receive) -> Receive:
    pending = list(messages)

    async def receive() -> AsgiMessage:
        if pending:
            return pending.pop(0)
        return await rest()

    return receive
