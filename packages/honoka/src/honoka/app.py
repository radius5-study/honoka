"""The App: route registration, registration-time chain composition, and the ASGI entry."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from honoka.asgi import AsgiMessage, AsgiScope, HttpScope, Receive, Send, run_lifespan
from honoka.context import Context, Key
from honoka.errors import HTTPError, default_error_response
from honoka.json import STDLIB_JSON, JsonCodec
from honoka.request import Request
from honoka.response import FileResponse, Response, Stream
from honoka.router import (
    NO_PARAMS,
    Chain,
    Route,
    RouteInfo,
    RouterFactory,
    RouterLike,
    SmartRouter,
    normalize,
    parse_pattern,
    split,
)
from honoka.types import Handler, Middleware, Next

if TYPE_CHECKING:
    from honoka.testing import Client


type Hook = Callable[[], Awaitable[None] | None]

# The router every App uses unless it is given `router=`; tests swap it to run on each router.
DEFAULT_ROUTER: RouterFactory = SmartRouter

ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


class _Next:
    """The `call_next` handed to a middleware. One small object per request per layer."""

    __slots__ = ("_c", "_step")

    def __init__(self, c: Context, step: Chain) -> None:
        self._c = c
        self._step = step

    async def __call__(self) -> Response:
        return await self._step(self._c)


def _is_async(fn: object) -> bool:
    return bool(
        inspect.iscoroutinefunction(fn)
        or (callable(fn) and inspect.iscoroutinefunction(type(fn).__call__))
    )


async def _run_hook(hook: Hook) -> None:
    result = hook()
    if result is not None:
        await result


def _matches(pattern: str, target: str) -> bool:
    """Whether the middleware `pattern` (`/api/*`, `/admin`, `*`) applies to `target`."""
    if pattern in ("*", "/*"):
        return True
    pattern, target = normalize(pattern), normalize(target)
    wildcard = pattern.endswith("/*")
    base = split(pattern[:-2] if wildcard else pattern)
    parts = split(target)
    return parts[: len(base)] == base if wildcard else parts == base


class App:
    def __init__(
        self,
        *,
        json_codec: JsonCodec = STDLIB_JSON,
        strict: bool = True,
        router: RouterFactory | None = None,
        threads: bool = True,
    ) -> None:
        """`strict=False` makes `/a` and `/a/` the same path (Hono's `strict` option). `router`
        picks the matching strategy: `SmartRouter` (default), `RegExpRouter` or `TrieRouter`.
        `threads=False` runs `def` handlers on the event loop, for hosts without threads
        (Cloudflare Python Workers)."""
        self._codec = json_codec
        self._strict = strict
        self._threads = threads
        self._router_factory = DEFAULT_ROUTER if router is None else router
        self._routes: list[Route] = []
        self._middlewares: list[tuple[str, Middleware]] = []
        self._on_error: Handler | None = None
        self._not_found: Handler | None = None
        self._provided: dict[object, object] = {}
        self._router: RouterLike | None = None
        self._seen: set[tuple[str, str]] = set()
        self._startup: list[Hook] = []
        self._shutdown: list[Hook] = []
        self._started = False
        self._start_lock: asyncio.Lock | None = None

    # -- registration -------------------------------------------------------------------------

    def _route[H: Handler](
        self,
        methods: Sequence[str],
        path: str,
        middleware: tuple[Middleware, ...],
        inline: bool,
        meta: Mapping[str, object],
    ) -> Callable[[H], H]:
        verbs = tuple(dict.fromkeys(m.upper() for m in methods))
        if not verbs:
            raise ValueError("a route needs at least one HTTP method")
        parse_pattern(path)  # a bad pattern fails here, at the decorator, not on a request

        def decorator(handler: H) -> H:
            for verb in verbs:
                self._add(Route(verb, path, handler, middleware, inline, dict(meta)))
            return handler

        return decorator

    def _add(self, route: Route) -> None:
        pattern = route.pattern
        if not self._strict and len(pattern) > 1 and pattern.endswith("/"):
            pattern = pattern[:-1]
        if (route.method, pattern) in self._seen:
            raise ValueError(f"duplicate route: {route.method} {route.pattern}")
        for mw in route.middleware:
            if not _is_async(mw):
                raise TypeError(f"middleware {mw!r} must be an async function")
        self._seen.add((route.method, pattern))
        self._routes.append(route)
        self._router = None

    def route(self, prefix: str, sub: "App") -> None:
        """Mount `sub` under `prefix`. Its middleware applies only below the prefix; its
        `on_error`/`not_found` handlers are ignored (the parent app owns them)."""
        prefix = prefix.rstrip("/")
        for r in sub._routes:
            pattern = prefix if r.pattern == "/" else prefix + r.pattern
            self._add(Route(r.method, pattern, r.handler, r.middleware, r.inline, r.meta))
        for pattern, mw in sub._middlewares:
            self._middlewares.append(
                (prefix + "/*" if pattern in ("*", "/*") else prefix + pattern, mw)
            )
        self._router = None

    def on_startup[F: Hook](self, hook: F) -> F:
        self._startup.append(hook)
        return hook

    def on_shutdown[F: Hook](self, hook: F) -> F:
        self._shutdown.append(hook)
        return hook

    def provide[T](self, key: Key[T], value: T) -> None:
        """Publish an app-wide value, read by handlers with `c.get(key)`."""
        self._provided[key] = value

    async def start(self) -> None:
        """Run startup hooks once. The server calls this; tests use `app.test()`."""
        if self._started:
            return
        self.finalize()
        for hook in self._startup:
            await _run_hook(hook)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for hook in reversed(self._shutdown):
            await _run_hook(hook)

    def get[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        return self._route(("GET",), path, middleware, inline, meta)

    def post[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        return self._route(("POST",), path, middleware, inline, meta)

    def put[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        return self._route(("PUT",), path, middleware, inline, meta)

    def patch[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        return self._route(("PATCH",), path, middleware, inline, meta)

    def delete[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        return self._route(("DELETE",), path, middleware, inline, meta)

    def head[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        return self._route(("HEAD",), path, middleware, inline, meta)

    def options[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        return self._route(("OPTIONS",), path, middleware, inline, meta)

    def on[H: Handler](
        self,
        methods: str | Sequence[str],
        path: str,
        *middleware: Middleware,
        inline: bool = False,
        **meta: object,
    ) -> Callable[[H], H]:
        """Register one handler for several methods: `@app.on(["GET", "POST"], "/form")`."""
        verbs = (methods,) if isinstance(methods, str) else tuple(methods)
        return self._route(verbs, path, middleware, inline, meta)

    def all[H: Handler](
        self, path: str, *middleware: Middleware, inline: bool = False, **meta: object
    ) -> Callable[[H], H]:
        """Register one handler for every common method (not `CONNECT` or `TRACE`)."""
        return self._route(ALL_METHODS, path, middleware, inline, meta)

    def use(self, path: str, middleware: Middleware) -> None:
        """Wrap every route (and unmatched request) whose path matches `path`, e.g. `/api/*`."""
        if not _is_async(middleware):
            raise TypeError(f"middleware {middleware!r} must be an async function")
        self._middlewares.append((path, middleware))
        self._router = None

    def on_error[H: Handler](self, handler: H) -> H:
        """Decide the response for an error; it is read from `c.error`."""
        self._on_error = handler
        return handler

    def not_found[H: Handler](self, handler: H) -> H:
        """Answer requests that match no route."""
        self._not_found = handler
        return handler

    def route_table(self) -> list[RouteInfo]:
        return [
            RouteInfo(r.method, r.pattern, r.handler, r.middleware, r.meta) for r in self._routes
        ]

    def finalize(self) -> None:
        """Compile routes now, so registration mistakes surface at startup, not on a request."""
        self._compile()

    def test(self) -> "Client":
        """A synchronous test client that drives the app through its ASGI entry."""
        from honoka.testing import Client

        return Client(self)

    # -- compilation --------------------------------------------------------------------------

    def _guard(self, step: Chain) -> Chain:
        """Turn an exception raised by `step` into a response, so outer layers still see one."""

        async def guarded(c: Context) -> Response:
            try:
                return await step(c)
            except Exception as exc:  # noqa: BLE001 - the framework boundary catches everything
                return await self._handle_error(c, exc)

        return guarded

    async def _handle_error(self, c: Context, exc: Exception) -> Response:
        c.error = exc
        if self._on_error is not None:
            try:
                return await self._call(self._on_error, c)
            except Exception as handler_exc:  # noqa: BLE001 - a failing handler falls back
                exc = handler_exc
        return default_error_response(exc)

    @staticmethod
    async def _call(handler: Handler, c: Context) -> Response:
        result = handler(c)
        if isinstance(result, Response):
            return result
        return await result

    def _endpoint(self, handler: Handler, inline: bool) -> Chain:
        if _is_async(handler):
            # An `async def` handler already is `(Context) -> Awaitable[Response]`.
            return cast("Chain", handler)
        if inline or not self._threads:

            async def run_inline(c: Context) -> Response:
                result = handler(c)
                assert isinstance(result, Response)
                return result

            return run_inline

        async def run_in_thread(c: Context) -> Response:
            result = await asyncio.to_thread(handler, c)
            assert isinstance(result, Response)
            return result

        return run_in_thread

    def _compose(self, middlewares: list[Middleware], endpoint: Chain) -> Chain:
        """Wrap `endpoint` in `middlewares`, outermost first.

        Each step a middleware calls is guarded, so the middleware sees an error response instead
        of an exception. The outermost step is guarded by `_dispatch`, saving one frame a request."""
        step = endpoint
        for mw in reversed(middlewares):
            step = self._layer(mw, self._guard(step))
        return step

    @staticmethod
    def _layer(mw: Middleware, step: Chain) -> Chain:
        async def run(c: Context) -> Response:
            next_: Next = _Next(c, step)
            return await mw(c, next_)

        return run

    def _compile(self) -> RouterLike:
        router = self._router_factory(strict=self._strict)
        for route in self._routes:
            applicable = [
                mw for pattern, mw in self._middlewares if _matches(pattern, route.pattern)
            ]
            applicable.extend(route.middleware)
            route.chain = self._compose(applicable, self._endpoint(route.handler, route.inline))
            router.add(route)
        router.compile()
        self._router = router
        return router

    # -- ASGI entry ---------------------------------------------------------------------------

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        """The ASGI entry: Granian, uvicorn and Cloudflare Python Workers
        (`asgi.entrypoint(app)`) all serve the app through it."""
        kind = scope["type"]
        if kind == "http":
            if not self._started:
                await self._ensure_started()
            await self._dispatch(cast(HttpScope, scope), receive, send)
        elif kind == "lifespan":
            await run_lifespan(self, receive, send)
        elif kind == "websocket":  # not supported yet: refuse the handshake politely
            await receive()
            await send({"type": "websocket.close", "code": 1000})

    async def _dispatch(self, scope: HttpScope, receive: Receive, send: Send) -> None:
        router = self._router or self._compile()
        method, path = scope["method"], scope["path"]
        found = router.match(method, path)
        head = found is None and method == "HEAD"
        if head:
            found = router.match("GET", path)
        params = NO_PARAMS if found is None else found[1]
        c = Context(Request(scope, receive, params, self._codec), self._provided, self._codec)
        if found is not None and found[0].chain is not None:
            c.route = found[0].pattern
            chain = found[0].chain
        else:
            wrapping = [mw for pattern, mw in self._middlewares if _matches(pattern, path)]
            chain = self._compose(wrapping, self._unmatched)
        try:
            response = await chain(c)
        except Exception as exc:  # noqa: BLE001 - the framework boundary catches everything
            response = await self._handle_error(c, exc)
        if c.set_cookies is not None:
            response.headers.extend(("set-cookie", v) for v in c.set_cookies)
        # UTF-8, as the servers do for str headers: a redirect to "/日本語" must not crash.
        start: AsgiMessage = {
            "type": "http.response.start",
            "status": response.status,
            "headers": [(k.lower().encode("latin-1"), v.encode()) for k, v in response.headers],
        }
        await send(start)
        if isinstance(response, Stream) and not head:
            async for chunk in response.chunks:
                data = chunk.encode() if isinstance(chunk, str) else chunk
                await send({"type": "http.response.body", "body": data, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            body = b""
            if head or isinstance(response, Stream):
                pass
            elif isinstance(response, FileResponse):
                # ASGI has no portable "send this file"; read it (Workers has no disk anyway).
                body = Path(response.path).read_bytes()
            else:
                body = response.body
            await send({"type": "http.response.body", "body": body})

    async def _ensure_started(self) -> None:
        """Hosts without a lifespan (Workers) never announce startup: do it on the first request."""
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            await self.start()

    async def _unmatched(self, c: Context) -> Response:
        allowed = self._router.allowed_methods(c.req.path) if self._router else []
        if not allowed:
            if self._not_found is not None:
                return await self._call(self._not_found, c)
            raise HTTPError(404, "not_found")
        allow = ("allow", ", ".join(allowed))
        if c.req.method == "OPTIONS":
            return Response(b"", 204, [allow])
        raise HTTPError(405, "method_not_allowed", headers=[allow])
