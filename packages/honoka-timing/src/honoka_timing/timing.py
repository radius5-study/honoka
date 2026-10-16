import time

from honoka import Context, Middleware, Next, Response


def timing(name: str = "app") -> Middleware:
    """Add a `Server-Timing` header with the time spent in the wrapped layers."""

    async def middleware(c: Context, call_next: Next) -> Response:
        start = time.perf_counter()
        response = await call_next()
        response.headers.append(
            ("server-timing", f"{name};dur={(time.perf_counter() - start) * 1000:.1f}")
        )
        return response

    return middleware
