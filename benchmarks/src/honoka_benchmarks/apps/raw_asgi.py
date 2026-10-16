"""Baseline for ASGI frameworks: a hand-written ASGI app."""

from starlette.types import Receive, Scope, Send

from honoka_benchmarks.apps.raw_core import extra_headers, handle


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] != "http":
        return
    headers = {k: v for k, v in scope["headers"] if k in (b"authorization", b"origin")}

    async def read_body() -> bytes:
        body = b""
        while True:
            message = await receive()
            body += bytes(message.get("body", b""))
            if not message.get("more_body"):
                return body

    authorization = headers.get(b"authorization")
    origin = headers.get(b"origin")
    status, body, content_type = await handle(
        str(scope["method"]),
        str(scope["path"]),
        bytes(scope["query_string"]).decode(),
        authorization.decode() if authorization else None,
        read_body,
    )
    response_headers = [
        (b"content-type", content_type.encode()),
        *((k.encode(), v.encode()) for k, v in extra_headers(origin.decode() if origin else None)),
    ]
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body})
