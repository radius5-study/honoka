from collections.abc import Callable, Sequence

from honoka import Context, HTTPError, Middleware, Next, Response
from honoka.router import normalize, split

type Condition = str | Callable[[Context], bool]


class _PassedError(Exception):
    """Raised through a middleware that called `next`: it let the request pass."""


def some(*middlewares: Middleware) -> Middleware:
    """Let the request through when ANY middleware passes (calls `next`); they run in order and
    the first pass wins. A middleware that raises `HTTPError` (other errors propagate), or answers without calling `next`, counts as
    failed and the next one is tried. When all fail the last failure is raised (or returned).

    Hono's `some` has the same shape. Limit: a passing middleware is cut off at its `next()`, so
    what it would do after `next()` returns does not run."""

    async def middleware(c: Context, call_next: Next) -> Response:
        failure: HTTPError | Response | None = None
        for mw in middlewares:

            async def probe() -> Response:
                raise _PassedError

            try:
                answer = await mw(c, probe)
            except _PassedError:
                return await call_next()
            except HTTPError as exc:
                failure = exc
            else:
                failure = answer
        if isinstance(failure, HTTPError):
            raise failure
        return failure if failure is not None else await call_next()

    return middleware


def every(*middlewares: Middleware) -> Middleware:
    """Run all middleware in order; the first one that raises or answers without calling `next`
    stops the chain. The request reaches the handler only if all of them pass."""

    async def middleware(c: Context, call_next: Next) -> Response:
        async def step(i: int) -> Response:
            if i == len(middlewares):
                return await call_next()

            async def following() -> Response:
                return await step(i + 1)

            return await middlewares[i](c, following)

        return await step(0)

    return middleware


def _matches(pattern: str, target: str) -> bool:
    # The rule of `app.use` patterns: `*`, `/api/*` (the prefix and below) or an exact path.
    if pattern in ("*", "/*"):
        return True
    pattern, target = normalize(pattern), normalize(target)
    wildcard = pattern.endswith("/*")
    base = split(pattern[:-2] if wildcard else pattern)
    parts = split(target)
    return parts[: len(base)] == base if wildcard else parts == base


def except_(condition: Condition | Sequence[Condition], middleware: Middleware) -> Middleware:
    """Run `middleware` unless the request matches `condition`: a path pattern (`/api/*`, exact
    paths), a predicate `(c) -> bool`, or a list of either. (`except` is a Python keyword.)"""
    conditions = [condition] if isinstance(condition, str) or callable(condition) else condition

    def skipped(c: Context) -> bool:
        return any(
            _matches(cond, c.req.path) if isinstance(cond, str) else cond(c) for cond in conditions
        )

    async def wrapper(c: Context, call_next: Next) -> Response:
        if skipped(c):
            return await call_next()
        return await middleware(c, call_next)

    return wrapper
