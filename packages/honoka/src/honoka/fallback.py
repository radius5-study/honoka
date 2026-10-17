"""`on_error` and `not_found` registrations: routable, stacked, and written as middleware steps
(Hono v5). Each registration may take a path; several registrations stack in order, deeper
sub-apps first; a step answers or calls `next()` to hand over to the next one, and when the steps
run out the built-in answer is used."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from honoka.context import Context
from honoka.response import Response
from honoka.types import Handler, Middleware, Next

type Step = Callable[[Context, Next], Awaitable[Response]]
type Fallback = Handler | Middleware
type Matches = Callable[[str, str], bool]


def takes_next(fn: Fallback) -> bool:
    """A callable that wants `(c, next)` is a middleware step; `(c)` alone is a final handler."""
    positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    required = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in positional and p.default is p.empty
    ]
    return len(required) >= 2


def _step(fn: Fallback) -> Step:
    if takes_next(fn):
        return cast(Step, fn)
    handler = cast(Handler, fn)

    async def final(c: Context, call_next: Next) -> Response:
        result = handler(c)
        return result if isinstance(result, Response) else await result

    return final


@dataclass(frozen=True, slots=True)
class _Registration:
    pattern: str
    steps: tuple[Step, ...]
    depth: int


class Fallbacks:
    def __init__(self, matches: Matches) -> None:
        self._matches = matches
        self._items: list[_Registration] = []

    def add(self, args: tuple[str | Fallback, ...], base: str = "") -> None:
        pattern = "*"
        if args and isinstance(args[0], str):
            pattern, args = args[0], args[1:]
        if base:
            pattern = base + "/*" if pattern in ("*", "/*") else base + pattern
        steps = tuple(_step(a) for a in args if not isinstance(a, str))
        if not steps:
            raise TypeError("expected at least one handler")
        self._items.append(_Registration(pattern, steps, 0))

    def mount(self, prefix: str, sub: "Fallbacks") -> None:
        """Import a sub-app's registrations below `prefix`; they run before the parent's."""
        for item in sub._items:
            pattern = prefix + "/*" if item.pattern in ("*", "/*") else prefix + item.pattern
            self._items.append(_Registration(pattern, item.steps, item.depth + 1))

    async def run(self, c: Context, builtin: Callable[[], Response]) -> Response:
        """Run the steps whose path covers the request, deepest sub-app first."""
        path = c.req.path
        covering = [i for i in self._items if self._matches(i.pattern, path)]
        covering.sort(key=lambda i: -i.depth)  # stable: registration order within a depth
        steps = [s for i in covering for s in i.steps]
        position = 0

        async def call_next() -> Response:
            nonlocal position
            if position == len(steps):
                return builtin()
            step = steps[position]
            position += 1
            return await step(c, call_next)

        return await call_next()
