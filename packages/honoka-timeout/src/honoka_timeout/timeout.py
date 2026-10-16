import asyncio

from honoka import Context, HTTPError, Middleware, Next, Response


def timeout(seconds: float) -> Middleware:
    """Answer 504 when the wrapped layers take longer than `seconds`.

    A sync (`def`) handler runs in a thread and cannot be cancelled: the client gets the 504, but
    the thread finishes its work."""

    async def middleware(c: Context, call_next: Next) -> Response:
        limit = asyncio.timeout(seconds)
        try:
            async with limit:
                return await call_next()
        except TimeoutError:
            if limit.expired():
                raise HTTPError(504, "timeout") from None
            raise

    return middleware
