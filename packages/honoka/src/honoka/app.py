"""The App: route registration, registration-time chain composition, and the ASGI entry."""

import asyncio
import copy
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast, overload

from honoka.asgi import AsgiMessage, AsgiScope, HttpScope, Receive, Send, run_lifespan
from honoka.context import Context, Key
from honoka.errors import HTTPError, NotFound, default_error_response, logger
from honoka.fallback import Fallback, Fallbacks, takes_next
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
    expand_optional,
    normalize,
    parse_pattern,
    split,
)
from honoka.types import Handler, Middleware, Next
from honoka.websocket import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from honoka.testing import Client


type Hook = Callable[[], Awaitable[None] | None]

# The router every App uses unless it is given `router=`; tests swap it to run on each router.
DEFAULT_ROUTER: RouterFactory = SmartRouter

ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

type GetPath = Callable[[HttpScope], str]
type WebSocketHandler = Callable[[WebSocket], Awaitable[None]]
type AsgiApp = Callable[[AsgiScope, Receive, Send], Awaitable[None]]


class _Shared:
    """State that `base_path()` views of one app share: what was compiled and whether it ran."""

    __slots__ = ("mounts", "router", "started", "ws_router")

    def __init__(self) -> None:
        self.router: RouterLike | None = None
        self.ws_router: RouterLike | None = None
        self.mounts: list[tuple[str, AsgiApp, Chain]] = []
        self.started = False


class _Pass(Response):
    """What the end of a gate's middleware chain returns: the request may go on."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(b"", 200)


class _Next:
    """The `call_next` handed to a middleware. One small object per request per layer."""

    __slots__ = ("_c", "_step")

    def __init__(self, c: Context, step: Chain) -> None:
        self._c = c
        self._step = step

    async def __call__(self) -> Response:
        response = await self._step(self._c)
        if self._c._header_ops is not None:
            self._c.apply_headers(response)
        return response


def _is_async(fn: object) -> bool:
    return bool(
        inspect.iscoroutinefunction(fn)
        or (callable(fn) and inspect.iscoroutinefunction(type(fn).__call__))
    )


async def _run_hook(hook: Hook) -> None:
    result = hook()
    if result is not None:
        await result


def _applicability(middleware: str, route: str) -> str:
    """Whether a middleware pattern covers every path of a route (`always`), none (`never`), or
    some, which only the request path can decide (`maybe`)."""
    if middleware in ("*", "/*"):
        return "always"
    if _matches(middleware, route):
        return "always"
    if not route.startswith("/"):
        return "maybe"
    wanted = split(normalize(middleware))
    wildcard = bool(wanted) and wanted[-1] == "*"
    if wildcard:
        wanted = wanted[:-1]
    segments = parse_pattern(route)
    for want, got in zip(wanted, segments, strict=False):
        if got.kind == "literal" and got.text != want:
            return "never"
    if (
        not wildcard
        and len(segments) > len(wanted)
        and all(seg.kind == "literal" for seg in segments)
    ):
        return "never"
    return "maybe"


def _matches(pattern: str, target: str) -> bool:
    """Whether the middleware `pattern` (`/api/*`, `/admin`, `*`) applies to `target`."""
    if pattern in ("*", "/*"):
        return True
    pattern, target = normalize(pattern), normalize(target)
    wildcard = pattern.endswith("/*")
    base = split(pattern[:-2] if wildcard else pattern)
    parts = split(target)
    return parts[: len(base)] == base if wildcard else parts == base


def _gated(pattern: str, mw: Middleware) -> Middleware:
    """`mw`, but only for requests whose path `pattern` covers."""

    async def gated(c: Context, call_next: Next) -> Response:
        if _matches(pattern, c.req.path):
            return await mw(c, call_next)
        return await call_next()

    return gated


class App:
    def __init__(
        self,
        *,
        json_codec: JsonCodec = STDLIB_JSON,
        strict: bool = True,
        router: RouterFactory | None = None,
        get_path: GetPath | None = None,
    ) -> None:
        """`strict=False` makes `/a` and `/a/` the same path (Hono's `strict` option). `router`
        picks the matching strategy: `SmartRouter` (default), `RegExpRouter` or `TrieRouter`.
        `get_path` decides which path is routed, e.g. to route on the host as well."""
        self._codec = json_codec
        self._strict = strict
        self._router_factory = DEFAULT_ROUTER if router is None else router
        self._routes: list[Route] = []
        self._middlewares: list[tuple[str, Middleware]] = []
        self._on_error = Fallbacks(_matches)
        self._not_found = Fallbacks(_matches)
        self._provided: dict[object, object] = {}
        self._shared = _Shared()
        self._seen: set[tuple[str, str]] = set()
        self._startup: list[Hook] = []
        self._shutdown: list[Hook] = []
        self._get_path = get_path
        self._base = ""
        self._mounts: list[tuple[str, AsgiApp]] = []
        self._websockets: list[tuple[str, WebSocketHandler]] = []

    @property
    def _router(self) -> RouterLike | None:
        return self._shared.router

    @_router.setter
    def _router(self, value: RouterLike | None) -> None:
        self._shared.router = value

    @property
    def _started(self) -> bool:
        return self._shared.started

    @_started.setter
    def _started(self, value: bool) -> None:
        self._shared.started = value

    def base_path(self, prefix: str) -> Self:
        """A view of this app whose routes, middleware and fallbacks all sit below `prefix`
        (Hono's `basePath`). Both objects serve the same routes."""
        view = copy.copy(self)
        view._base = self._base + prefix.rstrip("/")
        return view

    # -- registration -------------------------------------------------------------------------

    def _route(
        self,
        methods: Sequence[str],
        path: str | Sequence[str],
        steps: tuple[Middleware | Handler, ...],
        inline: bool,
        meta: Mapping[str, object],
    ) -> None:
        """Register `steps[-1]` as the handler and the steps before it as its middleware."""
        verbs = tuple(dict.fromkeys(m.upper() for m in methods))
        if not verbs:
            raise ValueError("a route needs at least one HTTP method")
        if not steps or takes_next(steps[-1]):
            raise TypeError(
                "the last argument must be the handler: a function of one parameter (c)"
            )
        handler = cast(Handler, steps[-1])
        middleware = cast(tuple[Middleware, ...], steps[:-1])
        paths = [path] if isinstance(path, str) else list(path)
        patterns = [
            self._base + variant if variant != "/" or not self._base else self._base
            for given in paths
            for variant in expand_optional(given)
        ]
        for pattern in patterns:
            parse_pattern(pattern)  # a bad pattern fails here, before anything is registered
        for pattern in patterns:
            for verb in verbs:
                self._add(Route(verb, pattern, handler, middleware, inline, dict(meta)))

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
        `on_error`/`not_found` registrations come along and run before the parent's."""
        prefix = self._base + prefix.rstrip("/")
        for r in sub._routes:
            pattern = prefix if r.pattern == "/" else prefix + r.pattern
            self._add(Route(r.method, pattern, r.handler, r.middleware, r.inline, r.meta))
        for pattern, mw in sub._middlewares:
            self._middlewares.append(
                (prefix + "/*" if pattern in ("*", "/*") else prefix + pattern, mw)
            )
        self._on_error.mount(prefix, sub._on_error)
        self._not_found.mount(prefix, sub._not_found)
        self._websockets.extend((prefix + path, h) for path, h in sub._websockets)
        self._mounts.extend((prefix + path, app) for path, app in sub._mounts)
        self._router = None

    def mount(self, prefix: str, app: AsgiApp) -> None:
        """Serve another ASGI application below `prefix`. It sees the path without the prefix
        (and the prefix in `root_path`). Mounted apps answer before this app's routes and
        middleware run."""
        full = self._base + prefix.rstrip("/")
        if not full:
            raise ValueError("mount needs a prefix other than '/': it would swallow every route")
        self._mounts.append((full, app))

    def ws(self, path: str, handler: WebSocketHandler) -> None:
        """Register a WebSocket handler: `async def chat(ws: WebSocket)`. It must `accept()`."""
        pattern = self._base + path
        parse_pattern(pattern)
        self._websockets.append((pattern, handler))
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

    @overload
    def query(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def query(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def query(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        """The HTTP `QUERY` method: a safe request that carries a body."""
        self._route(("QUERY",), path, steps, inline, meta)

    @overload
    def get(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def get(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def get(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        self._route(("GET",), path, steps, inline, meta)

    @overload
    def post(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def post(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def post(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        self._route(("POST",), path, steps, inline, meta)

    @overload
    def put(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def put(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def put(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        self._route(("PUT",), path, steps, inline, meta)

    @overload
    def patch(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def patch(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def patch(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        self._route(("PATCH",), path, steps, inline, meta)

    @overload
    def delete(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def delete(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def delete(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        self._route(("DELETE",), path, steps, inline, meta)

    @overload
    def head(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def head(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def head(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        self._route(("HEAD",), path, steps, inline, meta)

    @overload
    def options(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def options(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def options(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        self._route(("OPTIONS",), path, steps, inline, meta)

    @overload
    def on(
        self,
        methods: str | Sequence[str],
        path: str | Sequence[str],
        handler: Handler,
        /,
        *,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    @overload
    def on(
        self,
        methods: str | Sequence[str],
        path: str | Sequence[str],
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def on(
        self,
        methods: str | Sequence[str],
        path: str | Sequence[str],
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None:
        """Register one handler for several methods and paths: `app.on(["GET", "POST"], ["/form", "/ja/form"], handler)`.
        A method may be any token, e.g. `"PURGE"`."""
        verbs = (methods,) if isinstance(methods, str) else tuple(methods)
        self._route(verbs, path, steps, inline, meta)

    @overload
    def all(
        self, path: str, handler: Handler, /, *, inline: bool = False, **meta: object
    ) -> None: ...

    @overload
    def all(
        self,
        path: str,
        first: Middleware,
        /,
        *steps: Middleware | Handler,
        inline: bool = False,
        **meta: object,
    ) -> None: ...

    def all(
        self, path: str, *steps: Middleware | Handler, inline: bool = False, **meta: object
    ) -> None:
        """Register one handler for every common method (not `CONNECT` or `TRACE`)."""
        self._route(ALL_METHODS, path, steps, inline, meta)

    def use(self, path: str, middleware: Middleware) -> None:
        """Wrap every route (and unmatched request) whose path matches `path`, e.g. `/api/*`."""
        if not _is_async(middleware):
            raise TypeError(f"middleware {middleware!r} must be an async function")
        self._middlewares.append((self._scoped(path), middleware))
        self._router = None

    def _scoped(self, path: str) -> str:
        """A middleware or fallback path below this view's base path."""
        if not self._base:
            return path
        return self._base + "/*" if path in ("*", "/*") else self._base + path

    @overload
    def on_error[F: Fallback](self, handler: F, /) -> F: ...

    @overload
    def on_error(self, first: str | Fallback, /, *rest: Fallback) -> None: ...

    def on_error(self, *steps: str | Fallback) -> Fallback | None:
        """Decide the response for an error, read from `c.error`. Steps are `(c)` handlers or
        `(c, next)` middleware, optionally scoped by a path first; registrations stack."""
        self._on_error.add(steps, self._base)
        return steps[0] if len(steps) == 1 and not isinstance(steps[0], str) else None

    @overload
    def not_found[F: Fallback](self, handler: F, /) -> F: ...

    @overload
    def not_found(self, first: str | Fallback, /, *rest: Fallback) -> None: ...

    def not_found(self, *steps: str | Fallback) -> Fallback | None:
        """Answer requests that match no route, and `c.not_found()`. Same shape as `on_error`."""
        self._not_found.add(steps, self._base)
        return steps[0] if len(steps) == 1 and not isinstance(steps[0], str) else None

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
        if isinstance(exc, NotFound):
            return await self._answer_not_found(c)
        c.error = exc
        try:
            return await self._on_error.run(c, lambda: default_error_response(exc))
        except Exception as handler_exc:  # noqa: BLE001 - a failing handler falls back
            return default_error_response(handler_exc)

    async def _answer_not_found(self, c: Context) -> Response:
        try:
            return await self._not_found.run(c, lambda: default_error_response(NotFound()))
        except Exception as exc:  # noqa: BLE001 - a failing handler falls back
            return default_error_response(exc)

    def _endpoint(self, handler: Handler, inline: bool) -> Chain:
        if _is_async(handler):
            # An `async def` handler already is `(Context) -> Awaitable[Response]`.
            return cast("Chain", handler)
        if inline:

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

    def _applicable(self, route: Route) -> list[Middleware]:
        """The middleware that runs for `route`, each gated by the request path when the route's
        pattern leaves open whether the middleware's pattern covers it."""
        applicable: list[Middleware] = []
        for pattern, mw in self._middlewares:
            fit = _applicability(pattern, route.pattern)
            if fit == "always":
                applicable.append(mw)
            elif fit == "maybe":
                applicable.append(_gated(pattern, mw))
        applicable.extend(route.middleware)
        return applicable

    @staticmethod
    async def _pass(c: Context) -> Response:
        return _Pass()

    def _compile(self) -> RouterLike:
        router = self._router_factory(strict=self._strict)
        for route in self._routes:
            route.chain = self._compose(
                self._applicable(route), self._endpoint(route.handler, route.inline)
            )
            router.add(route)
        router.compile()
        ws_router = self._router_factory(strict=self._strict)
        for pattern, ws_handler in self._websockets:
            gate = Route("WS", pattern, cast(Handler, ws_handler), (), False, {})
            gate.chain = self._compose(self._applicable(gate), self._pass)
            ws_router.add(gate)
        ws_router.compile()
        self._shared.ws_router = ws_router
        self._shared.mounts = []
        for prefix, app in self._mounts:
            gate = Route("MOUNT", prefix + "/*", self._pass, (), False, {})
            self._shared.mounts.append(
                (prefix, app, self._compose(self._applicable(gate), self._pass))
            )
        self._router = router
        return router

    # -- ASGI entry ---------------------------------------------------------------------------

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        """The ASGI entry: any ASGI server serves the app through it."""
        kind = scope["type"]
        if kind == "lifespan":
            await run_lifespan(self, receive, send)
            return
        if self._mounts and await self._mounted(scope, receive, send):
            return
        if kind == "http":
            await self._dispatch(cast(HttpScope, scope), receive, send)
        elif kind == "websocket":
            await self._serve_websocket(scope, receive, send)

    async def _gate(
        self, scope: AsgiScope, path: str, chain: Chain, receive: Receive
    ) -> tuple[Context, Response | None]:
        """Run the middleware that covers a mount or a WebSocket. `None` means it may go on."""
        gate_scope = cast(HttpScope, {"method": "GET", **scope})
        c = Context(
            Request(gate_scope, receive, NO_PARAMS, self._codec, path), self._provided, self._codec
        )
        try:
            response = await chain(c)
        except Exception as exc:  # noqa: BLE001 - the framework boundary catches everything
            response = await self._handle_error(c, exc)
        return c, None if isinstance(response, _Pass) else response

    async def _mounted(self, scope: AsgiScope, receive: Receive, send: Send) -> bool:
        """Hand the request to a mounted ASGI app if its prefix covers the path, after the
        middleware that covers the prefix has let it through."""
        if self._router is None:
            self._compile()
        path = cast(str, scope["path"])
        for prefix, app, chain in self._shared.mounts:
            if path == prefix or path.startswith(prefix + "/"):
                c, refused = await self._gate(scope, path, chain, receive)
                if refused is not None:
                    if scope["type"] == "websocket":
                        await receive()
                        await send({"type": "websocket.close", "code": 1008})
                    else:
                        await self._emit(c, refused, scope["method"] == "HEAD", send)
                    return True
                inner = dict(scope)
                inner["path"] = path[len(prefix) :] or "/"
                raw = scope.get("raw_path")
                if isinstance(raw, bytes) and raw.startswith(prefix.encode()):
                    inner["raw_path"] = raw[len(prefix) :] or b"/"
                inner["root_path"] = cast(str, scope.get("root_path", "")) + prefix
                await app(inner, receive, send)
                return True
        return False

    async def _serve_websocket(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        if self._router is None:
            self._compile()
        router = self._shared.ws_router
        path = (
            cast(str, scope["path"])
            if self._get_path is None
            else self._get_path(cast(HttpScope, {"method": "GET", **scope}))
        )
        found = None if router is None else router.match("WS", path)
        if found is None or found[0].chain is None:
            await receive()  # the connect event; closing before accept answers 403
            await send({"type": "websocket.close", "code": 1000})
            return
        _, refused = await self._gate(scope, path, found[0].chain, receive)
        if refused is not None:
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        socket = WebSocket(scope, receive, send, found[1], path)
        handler = cast(WebSocketHandler, found[0].handler)
        try:
            await handler(socket)
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 - the framework boundary catches everything
            logger.error("unhandled websocket error", exc_info=exc)
            await socket.close(1011)
            return
        await socket.close()

    async def _dispatch(self, scope: HttpScope, receive: Receive, send: Send) -> None:
        router = self._router or self._compile()
        method = scope["method"]
        path = scope["path"] if self._get_path is None else self._get_path(scope)
        found = router.match(method, path)
        head = found is None and method == "HEAD"
        if head:
            found = router.match("GET", path)
        params = NO_PARAMS if found is None else found[1]
        c = Context(Request(scope, receive, params, self._codec, path), self._provided, self._codec)
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
        await self._emit(c, response, head, send)

    async def _emit(self, c: Context, response: Response, head: bool, send: Send) -> None:
        if c._header_ops is not None:
            c.apply_headers(response)
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
                # ASGI has no portable "send this file"; read it.
                body = Path(response.path).read_bytes()
            else:
                body = response.body
            await send({"type": "http.response.body", "body": body})

    async def _unmatched(self, c: Context) -> Response:
        allowed = self._router.allowed_methods(c.req.path) if self._router else []
        if not allowed:
            raise NotFound
        allow = ("allow", ", ".join(allowed))
        if c.req.method == "OPTIONS":
            return Response(b"", 204, [allow])
        raise HTTPError(405, "method_not_allowed", headers=[allow])
