"""Method-aware path matching: a dict for static paths and a trie for `{param}` segments."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from honoka.context import Context
from honoka.response import Response
from honoka.types import Handler, Middleware

type Chain = Callable[[Context], Awaitable[Response]]

NO_PARAMS: Mapping[str, str] = MappingProxyType(dict[str, str]())


def split(path: str) -> list[str]:
    return path.split("/")[1:]


def is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


@dataclass(frozen=True, slots=True)
class RouteInfo:
    method: str
    path: str
    handler: Handler
    middleware: tuple[Middleware, ...]
    meta: Mapping[str, object]


class Route:
    __slots__ = ("chain", "handler", "inline", "meta", "method", "middleware", "pattern")

    def __init__(
        self,
        method: str,
        pattern: str,
        handler: Handler,
        middleware: tuple[Middleware, ...],
        inline: bool,
        meta: Mapping[str, object],
    ) -> None:
        self.method = method
        self.pattern = pattern
        self.handler = handler
        self.middleware = middleware
        self.inline = inline
        self.meta = meta
        self.chain: Chain | None = None


class _Node:
    __slots__ = ("children", "param", "route")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.param: tuple[str, _Node] | None = None
        self.route: Route | None = None


class Router:
    def __init__(self) -> None:
        self._static: dict[tuple[str, str], Route] = {}
        self._roots: dict[str, _Node] = {}

    def add(self, route: Route) -> None:
        segments = split(route.pattern)
        if not any(is_param(s) for s in segments):
            if (route.method, route.pattern) in self._static:
                raise ValueError(f"duplicate route: {route.method} {route.pattern}")
            self._static[(route.method, route.pattern)] = route
            return
        node = self._roots.setdefault(route.method, _Node())
        for segment in segments:
            if is_param(segment):
                name = segment[1:-1]
                param = node.param
                if param is None:
                    param = node.param = (name, _Node())
                elif param[0] != name:
                    raise ValueError(
                        f"conflicting parameter names {param[0]!r} and {name!r} in {route.pattern}"
                    )
                node = param[1]
            else:
                node = node.children.setdefault(segment, _Node())
        if node.route is not None:
            raise ValueError(f"duplicate route: {route.method} {route.pattern}")
        node.route = route

    def match(self, method: str, path: str) -> tuple[Route, Mapping[str, str]] | None:
        route = self._static.get((method, path))
        if route is not None:
            return route, NO_PARAMS
        root = self._roots.get(method)
        if root is None:
            return None
        params: dict[str, str] = {}
        found = _walk(root, split(path), 0, params)
        return None if found is None else (found, params)

    def allowed_methods(self, path: str) -> list[str]:
        methods = {m for m, _ in self._static} | set(self._roots)
        allowed = {m for m in methods if self.match(m, path) is not None}
        if "GET" in allowed:
            allowed.add("HEAD")
        return sorted(allowed)


def _walk(node: _Node, segments: list[str], i: int, params: dict[str, str]) -> Route | None:
    if i == len(segments):
        return node.route
    child = node.children.get(segments[i])
    if child is not None:
        found = _walk(child, segments, i + 1, params)
        if found is not None:
            return found
    if node.param is not None:
        name, child = node.param
        params[name] = segments[i]
        found = _walk(child, segments, i + 1, params)
        if found is not None:
            return found
        del params[name]
    return None
