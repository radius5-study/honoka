"""Routers, modelled on Hono's: `RegExpRouter`, `TrieRouter` and the `SmartRouter` that picks one.

Paths without parameters are a dict lookup in every router. The rest go into a trie per method,
which the routers match differently:

- `RegExpRouter` renders the trie as one regular expression, so a request is matched by a single
  C-level `fullmatch`. Alternatives run from the most to the least specific and the regex engine's
  backtracking gives the priority.
- `TrieRouter` walks the trie in Python.
- `SmartRouter` (the default) tries its routers in order, `RegExpRouter` then `TrieRouter`, and
  keeps the first that accepts the whole set of routes. A router signals "cannot take this set" by
  raising `UnsupportedPathError`; any other error is a real mistake and propagates. As in Hono the
  choice is about what a router can express, never about speed.

All routers must give identical answers; the routing tests run against each of them."""

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Protocol, cast, override

from honoka.context import Context
from honoka.response import Response
from honoka.types import Handler, Middleware

type Chain = Callable[[Context], Awaitable[Response]]
type Matcher = Callable[[str], tuple[Route, Mapping[str, str]] | None]


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
# Most specific first: at one position `{id:int}` is tried before `{name}`. A regex of your own
# (`{code:re:[A-Z]{3}}`) sits between the fixed converters and plain `{name}`.
_ORDER = {name: i for i, name in enumerate(("int", "float", "uuid", "slug", "str", "path"))}
_REGEX_RANK = 3.5
_NAME = re.compile(r"[A-Za-z_]\w*")
_CONVERTER_RE = {name: re.compile(pattern, re.DOTALL) for name, pattern in CONVERTERS.items()}
_REGEX_PREFIX = "re:"


def _rank(converter: str) -> float:
    return _REGEX_RANK if converter.startswith(_REGEX_PREFIX) else _ORDER[converter]


def _converter_regex(converter: str) -> str:
    """The regular expression text a converter stands for (its own text for `re:...`)."""
    return (
        converter[len(_REGEX_PREFIX) :]
        if converter.startswith(_REGEX_PREFIX)
        else (CONVERTERS[converter])
    )


_compiled_regexes: dict[str, re.Pattern[str]] = {}


def _regex_of(converter: str) -> re.Pattern[str]:
    found = _CONVERTER_RE.get(converter) or _compiled_regexes.get(converter)
    if found is None:
        found = _compiled_regexes[converter] = re.compile(_converter_regex(converter), re.DOTALL)
    return found


def _context_dependent(regex: str) -> str | None:
    """Why `regex` would match differently inside the routers' one big expression than on its
    own, or `None`: anchors and lookbehinds look at what surrounds the parameter."""
    in_class = False
    i = 0
    while i < len(regex):
        ch = regex[i]
        if ch == "\\":
            if regex[i + 1 : i + 2] in ("A", "Z"):
                return f"\\{regex[i + 1]} anchors to the whole path"
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
            if regex[i + 1 : i + 2] == "^":
                i += 1
            if regex[i + 1 : i + 2] == "]":
                i += 1
        elif ch in "^$":
            return f"{ch!r} anchors are implied: the expression always matches the whole segment"
        elif regex.startswith(("(?<=", "(?<!"), i):
            return "lookbehind sees the text before the parameter, which differs between routers"
        i += 1
    return None


def split(path: str) -> list[str]:
    return path.split("/")[1:]


def canonical_path(path: str) -> str:
    """The path the router, the middleware gate and handlers all see: empty and `.` segments are
    dropped and `..` removes the segment before it (never climbing above the root); a trailing
    slash survives as a single one. `//admin/x` and `/a/../admin/x` are `/admin/x`, so a rule
    written for one spelling cannot be dodged by another."""
    if "//" not in path and "/." not in path:
        return path
    kept: list[str] = []
    for segment in path.split("/"):
        if segment == "..":
            if kept:
                kept.pop()
        elif segment not in ("", "."):
            kept.append(segment)
    trailing = path.endswith(("/", "/.", "/.."))
    return "/" + "/".join(kept) + ("/" if kept and trailing else "")


def _split_pattern(pattern: str) -> list[str]:
    """Split a route pattern on `/`, except inside `{...}` (a regex may contain a slash)."""
    parts: list[str] = []
    depth = 0
    start = 1
    for i, ch in enumerate(pattern):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
        elif ch == "/" and depth == 0 and i >= start:
            parts.append(pattern[start:i])
            start = i + 1
    parts.append(pattern[start:])
    return parts


def normalize(pattern: str) -> str:
    """The pattern as documentation shows it: `/users/{id:int}` -> `/users/{id}`, `{code:re:..}`
    -> `{code}`, an optional `{id}?` -> `{id}`; anonymous wildcards stay as they are."""
    if not pattern.startswith("/"):
        return pattern  # a middleware pattern such as "*"
    out: list[str] = []
    for part in _split_pattern(pattern):
        part = part.removesuffix("?") if part.endswith("}?") else part
        if part.startswith("{") and part.endswith("}"):
            part = "{" + part[1:-1].partition(":")[0] + "}"
        out.append(part)
    return "/" + "/".join(out)


def expand_optional(pattern: str) -> list[str]:
    """`/a/{x}?` is two routes: `/a` and `/a/{x}`. Only the last segment may be optional."""
    if not pattern.startswith("/"):
        return [pattern]
    parts = _split_pattern(pattern)
    for part in parts[:-1]:
        if part.endswith("}?"):
            raise ValueError(
                f"invalid route pattern {pattern!r}: only the last segment may be optional"
            )
    if not parts[-1].endswith("}?"):
        return [pattern]
    required = "/" + "/".join(parts[:-1])
    return [required, f"{required.rstrip('/')}/{parts[-1][:-1]}"]


@dataclass(frozen=True, slots=True)
class Segment:
    kind: str  # "literal", "param" or "wildcard"
    text: str  # the literal, or the parameter name
    converter: str = ""


def parse_pattern(pattern: str) -> list[Segment]:
    """Split a route pattern into segments, rejecting anything the router cannot match.

    A segment is a literal, `{name}`, `{name:converter}`, `{name:re:REGEX}`, a lone `*` (any one
    non-empty segment, or everything below when it is last) or `prefix*` (a segment starting with
    `prefix`). A `*` elsewhere inside a segment is an ordinary character."""

    def invalid(why: str) -> ValueError:
        return ValueError(f"invalid route pattern {pattern!r}: {why}")

    if not pattern.startswith("/"):
        raise invalid("it must start with '/'")
    parts = _split_pattern(pattern)
    segments: list[Segment] = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "*":
            segments.append(Segment("wildcard", "*") if last else Segment("param", "", "str"))
        elif part.startswith("{") and part.endswith("}"):
            name, _, converter = part[1:-1].partition(":")
            converter = converter or "str"
            if not _NAME.fullmatch(name):
                raise invalid(f"{part!r} has no valid parameter name")
            if converter.startswith(_REGEX_PREFIX):
                try:
                    compiled = re.compile(converter[len(_REGEX_PREFIX) :], re.DOTALL)
                except re.error as exc:
                    raise invalid(f"{part!r} is not a valid regular expression: {exc}") from exc
                reason = _context_dependent(converter[len(_REGEX_PREFIX) :])
                if reason is not None:
                    raise invalid(f"{part!r}: {reason}")
                if compiled.groups:
                    raise invalid(f"{part!r}: use (?:...) instead of capturing groups")
                if compiled.fullmatch("") is not None:
                    raise invalid(f"{part!r} must not match the empty string")
            elif converter not in CONVERTERS:
                raise invalid(f"unknown converter {converter!r}; use one of {sorted(CONVERTERS)}")
            elif converter == "path" and not last:
                raise invalid("a 'path' parameter must be the last segment")
            segments.append(Segment("param", name, converter))
        elif part.endswith("*") and "{" not in part and "}" not in part:
            segments.append(Segment("param", "", _REGEX_PREFIX + re.escape(part[:-1]) + "[^/]*"))
        elif "{" in part or "}" in part:
            raise invalid(f"parameters must fill a whole segment, got {part!r}")
        else:
            segments.append(Segment("literal", part))
    return segments


def path_params(pattern: str) -> list[tuple[str, str]]:
    """The (name, converter) of every parameter in `pattern`, in order."""
    return [(s.text, s.converter) for s in parse_pattern(pattern) if s.kind == "param" and s.text]


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
        for key in sorted(node.params, key=lambda k: _rank(k[1])):
            groups += 1
            number = groups
            named = (*trail, (key[0], number)) if key[0] else trail
            below = render(node.params[key], named)
            alternatives.append(f"/({_converter_regex(key[1])})" + below)
        if node.wildcard is not None:
            alternatives.append("(?:/.*)?" + mark(node.wildcard, trail))
        return "(?:" + "|".join(alternatives) + ")"

    return _Compiled(re.compile(render(root, ()), re.DOTALL), routes, params)


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
    node.order = tuple(sorted(node.params, key=lambda k: _rank(k[1])))
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
        if converter.startswith(_REGEX_PREFIX):
            # A regex may span several segments (`{file:re:.+\\.png}`): longest run first.
            regex = _regex_of(converter)
            for end in range(len(segments), i, -1):
                piece = "/".join(segments[i:end])
                if piece == "" or regex.fullmatch(piece) is None:
                    continue
                if name:
                    params[name] = piece
                found = _walk(below, segments, end, params)
                if found is not None:
                    return found
                if name:
                    del params[name]
            continue
        if converter == "str":
            if segment == "":  # a segment never contains "/", so non-empty is all `str` needs
                continue
        elif _CONVERTER_RE[converter].fullmatch(segment) is None:
            continue
        if name:
            params[name] = segment
        found = _walk(below, segments, i + 1, params)
        if found is not None:
            return found
        if name:
            del params[name]
    return node.wildcard


def _trie_matcher(root: _Node) -> Matcher:
    _prepare(root)

    def match(path: str) -> tuple[Route, Mapping[str, str]] | None:
        params: dict[str, str] = {}
        route = _walk(root, split(path), 0, params)
        return None if route is None else (route, params or NO_PARAMS)

    return match


class UnsupportedPathError(Exception):
    """A router cannot take this set of routes. `SmartRouter` then tries the next router."""


class RouterLike(Protocol):
    name: str

    def add(self, route: Route) -> None: ...

    def compile(self) -> None: ...

    def match(self, method: str, path: str) -> tuple[Route, Mapping[str, str]] | None: ...

    def allowed_methods(self, path: str) -> list[str]: ...


class RouterFactory(Protocol):
    def __call__(self, *, strict: bool = True) -> RouterLike: ...


class _BaseRouter:
    """What `RegExpRouter` and `TrieRouter` share: parsing, the static dict and the trie. A
    subclass only decides how a method's trie is matched."""

    name = "BaseRouter"

    def __init__(self, *, strict: bool = True) -> None:
        self._strict = strict
        self._static: dict[tuple[str, str], Route] = {}
        self._tries: dict[str, _Node] = {}
        self._matchers: dict[str, Matcher] = {}

    def _build(self, root: _Node) -> Matcher:
        raise NotImplementedError

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
        for segment in segments:
            if segment.kind == "literal":
                node = node.literals.setdefault(segment.text, _Node())
            elif segment.kind == "param":
                key = (segment.text, segment.converter)
                clash = next(
                    (n for n, c in node.params if c == key[1] and n and key[0] and n != key[0]),
                    None,
                )
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
            matcher = self._matchers[method] = self._build(root)
        return matcher

    def compile(self) -> None:
        """Build every method's matcher now, so an unsupported route set fails here."""
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


class TrieRouter(_BaseRouter):
    name = "TrieRouter"

    @override
    def _build(self, root: _Node) -> Matcher:
        return _trie_matcher(root)


class RegExpRouter(_BaseRouter):
    name = "RegExpRouter"

    @override
    def _build(self, root: _Node) -> Matcher:
        try:
            return _regex_matcher(root)
        except (re.error, RecursionError, OverflowError) as exc:
            raise UnsupportedPathError(str(exc)) from exc


class SmartRouter:
    """Tries `routers` in order and keeps the first that accepts every route (Hono's SmartRouter).

    Routes are buffered until `compile()`: each candidate gets all of them, and one that raises
    `UnsupportedPathError` is dropped for the next."""

    name = "SmartRouter"

    def __init__(
        self,
        *,
        strict: bool = True,
        routers: Sequence[RouterFactory] = (RegExpRouter, TrieRouter),
    ) -> None:
        self._strict = strict
        self._factories = tuple(routers)
        self._routes: list[Route] = []
        self._chosen: RouterLike | None = None

    @property
    def chosen(self) -> str | None:
        """The name of the router in use, once one has been chosen."""
        return None if self._chosen is None else self._chosen.name

    def add(self, route: Route) -> None:
        if self._chosen is not None:
            raise RuntimeError("cannot add a route after the router has been chosen")
        self._routes.append(route)

    def compile(self) -> None:
        if self._chosen is not None:
            return
        for factory in self._factories:
            candidate = factory(strict=self._strict)
            try:
                for route in self._routes:
                    candidate.add(route)
                candidate.compile()
            except UnsupportedPathError:
                continue
            self._chosen = candidate
            return
        raise RuntimeError("Fatal error: no router can take this set of routes")

    def _active(self) -> RouterLike:
        if self._chosen is None:
            self.compile()
        assert self._chosen is not None
        return self._chosen

    def match(self, method: str, path: str) -> tuple[Route, Mapping[str, str]] | None:
        return self._active().match(method, path)

    def allowed_methods(self, path: str) -> list[str]:
        return self._active().allowed_methods(path)


async def _never_called(c: Context) -> Response:
    raise AssertionError("a path matcher never runs its handler")


@lru_cache(maxsize=512)
def path_matcher(pattern: str, strict: bool = True) -> Callable[[str], bool]:
    """Whether a request path is covered by `pattern`, by the rules routing uses, so a middleware
    written for `/users/{id}/*` covers what a route of that pattern would match and nothing else.
    `*` and `/*` cover everything. A pattern the router refuses raises `ValueError` here."""
    if pattern in ("*", "/*"):
        return lambda path: True
    router = SmartRouter(strict=strict)
    for variant in expand_optional(pattern):
        parse_pattern(variant)  # a bad pattern fails here, with its own message
        router.add(Route("COVER", variant, _never_called, (), False, {}))
    router.compile()
    return lambda path: router.match("COVER", path) is not None
