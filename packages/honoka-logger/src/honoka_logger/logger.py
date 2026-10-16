import logging
import time

from honoka_request_id import REQUEST_ID

from honoka import Context, Middleware, Next, Response

access_log = logging.getLogger("honoka.access")


def logger(log: logging.Logger = access_log) -> Middleware:
    """Log `METHOD path status duration` (plus the request id when `request_id` ran first)."""

    async def middleware(c: Context, call_next: Next) -> Response:
        start = time.perf_counter()
        response = await call_next()
        elapsed = (time.perf_counter() - start) * 1000
        try:
            suffix = f" rid={c.get(REQUEST_ID)}"
        except LookupError:
            suffix = ""
        path = c.req.path.encode("unicode_escape").decode("ascii", "replace")
        log.info("%s %s %d %.1fms%s", c.req.method, path, response.status, elapsed, suffix)
        return response

    return middleware
