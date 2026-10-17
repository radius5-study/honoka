from collections.abc import Callable, Sequence

from honoka import Context, HTTPError, Middleware, Next, Response
from honoka.router import path_matcher

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


def _predicate(condition: Condition) -> Callable[[Context], bool]:
    if isinstance(condition, str):
        covers = path_matcher(condition)
        return lambda c: covers(c.req.path)
    return condition


def except_(condition: Condition | Sequence[Condition], middleware: Middleware) -> Middleware:
    """Run `middleware` unless the request matches `condition`: a path pattern (`/api/*`, exact
    paths), a predicate `(c) -> bool`, or a list of either. (`except` is a Python keyword.)"""
    given: Sequence[Condition]
    if isinstance(condition, str) or not isinstance(condition, Sequence):
        given = [condition]
    else:
        given = condition
    # Patterns are compiled now, so a bad one fails at registration. They follow the routing
    # rules with `strict` on: `/health/` is not `/health` here and the middleware then runs,
    # which is the safe side.
    conditions = [_predicate(cond) for cond in given]

    def skipped(c: Context) -> bool:
        return any(cond(c) for cond in conditions)

    async def wrapper(c: Context, call_next: Next) -> Response:
        if skipped(c):
            return await call_next()
        return await middleware(c, call_next)

    return wrapper
