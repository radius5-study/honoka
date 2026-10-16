import os
import re

from honoka import Context, Key, Middleware, Next, Response

REQUEST_ID: Key[str] = Key("request_id")

_VALID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def request_id(header: str = "x-request-id") -> Middleware:
    """Expose a request id as `REQUEST_ID` and echo it. A well-formed incoming id is reused;
    anything else is replaced, so a client cannot inject odd text into logs."""

    async def middleware(c: Context, call_next: Next) -> Response:
        incoming = c.req.header(header)
        rid = (
            incoming
            if incoming is not None and _VALID.fullmatch(incoming)
            else os.urandom(16).hex()
        )
        c.set(REQUEST_ID, rid)
        response = await call_next()
        response.headers.append((header, rid))
        return response

    return middleware
