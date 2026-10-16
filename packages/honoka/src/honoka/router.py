"""Method-aware path matching, in the style of Hono's RegExpRouter.

Paths without parameters are a dict lookup. For the rest, a method's routes are folded into a trie
and rendered as one regular expression, so a request is matched by a single C-level `fullmatch`
instead of a Python loop. Alternatives run from the most to the least specific, and the regex
engine's backtracking gives the same priority a hand-written matcher would.

CPython sizes every match result by the number of capturing groups, so one huge expression slows
every request down. Past `REGEX_ROUTE_LIMIT` routes a method is matched by walking the trie
instead, whose cost does not depend on how many routes there are (Hono's SmartRouter makes the
same trade). Both paths must give identical answers; the routing tests run under each."""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from honoka.context import Context
from honoka.response import Response
from honoka.types import Handler, Middleware

type Chain = Callable[[Context], Awaitable[Response]]
type Matcher = Callable[[str], tuple[Route, Mapping[str, str]] | None]

# Above this many parameterised routes for one method the trie walk beats the regex.
REGEX_ROUTE_LIMIT = 100

NO_PARAMS: Mapping[str, str] = MappingProxyType(dict[str, str]())


# What a `{name:converter}` segment may match. None of them contain a capturing group.
CONVERTERS: dict[str, str] = {
    "str": r"[^/]+",
    "int": r"[0-9]+",
    "float": r"[0-9]+(?:\.[0-9]+)?",
    "uuid": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    "slug": r"[A-Za-z0-9_-]+",
    "path": r".+",
}
# Most specific first: at one position `{id:int}` is tried before `{name}`.
_ORDER = {name: i for i, name in enumerate(("int", "float", "uuid", "slug", "str", "path"))}
_NAME = re.compile(r"[A-Za-z_]\w*")
_CONVERTER_RE = {name: re.compile(pattern) for name, pattern in CONVERTERS.items()}
_TYPED = re.compile(r"\{(\w+):\w+\}")


def split(path: str) -> list[str]:
    return path.split("/")[1:]


def normalize(pattern: str) -> str:
    """The pattern without converters: `/users/{id:int}` -> `/users/{id}`."""
    return _TYPED.sub(r"{\1}", pattern)


@dataclass(frozen=True, slots=True)
class Segment:
    kind: str  # "literal", "param" or "wildcard"
    text: str  # the literal, or the parameter name
    converter: str = ""


def parse_pattern(pattern: str) -> list[Segment]:
    """Split a route pattern into segments, rejecting anything the router cannot match."""

    def invalid(why: str) -> ValueError:
        return ValueError(f"invalid route pattern {pattern!r}: {why}")

    if not pattern.startswith("/"):
        raise invalid("it must start with '/'")
    parts = pattern.split("/")[1:]
    segments: list[Segment] = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "*":
            if not last:
                raise invalid("'*' must be the last segment")
            segments.append(Segment("wildcard", "*"))
        elif part.startswith("{") and part.endswith("}"):
            name, _, converter = part[1:-1].partition(":")
            converter = converter or "str"
            if not _NAME.fullmatch(name):
                raise invalid(f"{part!r} has no valid parameter name")
            if converter not in CONVERTERS:
                raise invalid(f"unknown converter {converter!r}; use one of {sorted(CONVERTERS)}")
            if converter == "path" and not last:
                raise invalid("a 'path' parameter must be the last segment")
            segments.append(Segment("param", name, converter))
        elif any(ch in part for ch in "{}*"):
            raise invalid(f"parameters and '*' must fill a whole segment, got {part!r}")
        else:
            segments.append(Segment("literal", part))
    return segments


def path_params(pattern: str) -> list[tuple[str, str]]:
    """The (name, converter) of every parameter in `pattern`, in order."""
    return [(s.text, s.converter) for s in parse_pattern(pattern) if s.kind == "param"]


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
    __slots__ = ("literals", "order", "params", "route", "wildcard")

    def __init__(self) -> None:
        self.order: tuple[tuple[str, str], ...] = ()  # params, most specific first (see _prepare)
        self.literals: dict[str, _Node] = {}
        self.params: dict[tuple[str, str], _Node] = {}  # (name, converter) -> child
        self.route: Route | None = None
        self.wildcard: Route | None = None


class _Compiled:
    __slots__ = ("params", "regex", "routes")

    def __init__(
        self,
        regex: re.Pattern[str],
        routes: dict[int, Route],
        params: dict[int, tuple[tuple[str, int], ...]],
    ) -> None:
        self.regex = regex
        self.routes = routes  # marker group number -> route
        self.params = params  # marker group number -> (name, group number) of its parameters


def _fold(rendered: dict[str, str]) -> str:
    """Join sibling literals into one alternation that shares their common prefixes.

    `v0|v1|v2` becomes `v(?:0|1|2)`. The regex engine can then discard a branch after one
    character, so matching stays cheap with hundreds of siblings instead of trying each in turn.
    `rendered` maps a literal to the pattern that follows it and must be in sorted order."""
    if len(rendered) == 1:
        ((literal, rest),) = rendered.items()
        return re.escape(literal) + rest
    by_first: dict[str, dict[str, str]] = {}
    alternatives: list[str] = []
    for literal, rest in rendered.items():
        if literal == "":
            alternatives.append(rest)  # the literal ended here; "" sorts first
        else:
            by_first.setdefault(literal[0], {})[literal[1:]] = rest
    alternatives += [re.escape(first) + _fold(tails) for first, tails in by_first.items()]
    return "(?:" + "|".join(alternatives) + ")"


def _compile(root: _Node) -> _Compiled:
    routes: dict[int, Route] = {}
    params: dict[int, tuple[tuple[str, int], ...]] = {}
    groups = 0

    def mark(route: Route, trail: tuple[tuple[str, int], ...]) -> str:
        """An empty capturing group that, when it is the last one matched, names the route."""
        nonlocal groups
        groups += 1
        routes[groups] = route
        params[groups] = trail
        return "()"

    def render(node: _Node, trail: tuple[tuple[str, int], ...]) -> str:
        # Groups are numbered by where their "(" appears, and alternatives are emitted in the
        # order they are generated here, so counting while generating gives the real numbers.
        nonlocal groups
        alternatives: list[str] = []
        if node.route is not None:
            alternatives.append(mark(node.route, trail))
        if node.literals:
            # Rendered in sorted order, which is also the order `_fold` writes them in, so the
            # group numbers handed out here match their position in the final pattern.
            rendered = {lit: render(node.literals[lit], trail) for lit in sorted(node.literals)}
            alternatives.append("/" + _fold(rendered))
        for key in sorted(node.params, key=lambda k: _ORDER[k[1]]):
            groups += 1
            number = groups
            below = render(node.params[key], (*trail, (key[0], number)))
            alternatives.append(f"/({CONVERTERS[key[1]]})" + below)
        if node.wildcard is not None:
            alternatives.append("(?:/.*)?" + mark(node.wildcard, trail))
        return "(?:" + "|".join(alternatives) + ")"

    return _Compiled(re.compile(render(root, ())), routes, params)


def _regex_matcher(root: _Node) -> Matcher:
    compiled = _compile(root)
    fullmatch = compiled.regex.fullmatch
    routes, params = compiled.routes, compiled.params

    def match(path: str) -> tuple[Route, Mapping[str, str]] | None:
        found = fullmatch(path)
        if found is None or found.lastindex is None:
            return None
        index = found.lastindex
        captured = params[index]
        if not captured:
            return routes[index], NO_PARAMS
        return routes[index], {name: cast(str, found[group]) for name, group in captured}

    return match


def _prepare(node: _Node) -> None:
    node.order = tuple(sorted(node.params, key=lambda k: _ORDER[k[1]]))
    for child in (*node.literals.values(), *node.params.values()):
        _prepare(child)


def _walk(node: _Node, segments: list[str], i: int, params: dict[str, str]) -> Route | None:
    if i == len(segments):
        return node.route if node.route is not None else node.wildcard
    segment = segments[i]
    child = node.literals.get(segment)
    if child is not None:
        found = _walk(child, segments, i + 1, params)
        if found is not None:
            return found
    for key in node.order:
        name, converter = key
        below = node.params[key]
        if converter == "path":  # always the last segment: it takes the rest of the path
            rest = "/".join(segments[i:])
            if rest != "" and below.route is not None:
                params[name] = rest
                return below.route
            continue
        if converter == "str":
            if segment == "":  # a segment never contains "/", so non-empty is all `str` needs
                continue
        elif _CONVERTER_RE[converter].fullmatch(segment) is None:
            continue
        params[name] = segment
        found = _walk(below, segments, i + 1, params)
        if found is not None:
            return found
        del params[name]
    return node.wildcard


def _trie_matcher(root: _Node) -> Matcher:
    _prepare(root)

    def match(path: str) -> tuple[Route, Mapping[str, str]] | None:
        params: dict[str, str] = {}
        route = _walk(root, split(path), 0, params)
        return None if route is None else (route, params or NO_PARAMS)

    return match


class Router:
    def __init__(self, *, strict: bool = True) -> None:
        self._strict = strict
        self._static: dict[tuple[str, str], Route] = {}
        self._tries: dict[str, _Node] = {}
        self._counts: dict[str, int] = {}
        self._matchers: dict[str, Matcher] = {}

    def _canonical(self, pattern: str) -> str:
        """Without `strict`, `/a/` and `/a` are one route."""
        if not self._strict and len(pattern) > 1 and pattern.endswith("/"):
            return pattern[:-1]
        return pattern

    def add(self, route: Route) -> None:
        pattern = self._canonical(route.pattern)
        segments = parse_pattern(pattern)
        if all(s.kind == "literal" for s in segments):
            if (route.method, pattern) in self._static:
                raise ValueError(f"duplicate route: {route.method} {route.pattern}")
            self._static[(route.method, pattern)] = route
            return
        node = self._tries.setdefault(route.method, _Node())
        self._matchers.pop(route.method, None)
        self._counts[route.method] = self._counts.get(route.method, 0) + 1
        for segment in segments:
            if segment.kind == "literal":
                node = node.literals.setdefault(segment.text, _Node())
            elif segment.kind == "param":
                key = (segment.text, segment.converter)
                clash = next((n for n, c in node.params if c == key[1] and n != key[0]), None)
                if clash is not None:
                    raise ValueError(
                        f"conflicting parameter names {clash!r} and {key[0]!r} in {route.pattern}"
                    )
                node = node.params.setdefault(key, _Node())
            else:
                if node.wildcard is not None:
                    raise ValueError(f"duplicate route: {route.method} {route.pattern}")
                node.wildcard = route
                return
        if node.route is not None:
            raise ValueError(f"duplicate route: {route.method} {route.pattern}")
        node.route = route

    def _matcher(self, method: str) -> Matcher | None:
        matcher = self._matchers.get(method)
        if matcher is None:
            root = self._tries.get(method)
            if root is None:
                return None
            use_regex = self._counts[method] <= REGEX_ROUTE_LIMIT
            matcher = self._matchers[method] = (
                _regex_matcher(root) if use_regex else _trie_matcher(root)
            )
        return matcher

    def compile(self) -> None:
        """Build every method's matcher now instead of on the first request."""
        for method in self._tries:
            self._matcher(method)

    def match(self, method: str, path: str) -> tuple[Route, Mapping[str, str]] | None:
        if not self._strict and len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        route = self._static.get((method, path))
        if route is not None:
            return route, NO_PARAMS
        matcher = self._matcher(method)
        return None if matcher is None else matcher(path)

    def allowed_methods(self, path: str) -> list[str]:
        methods = {m for m, _ in self._static} | set(self._tries)
        allowed = {m for m in methods if self.match(m, path) is not None}
        if "GET" in allowed:
            allowed.add("HEAD")
        return sorted(allowed)
