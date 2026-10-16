"""`honoka routes` and `honoka check`: inspect an app without serving it."""

import argparse
import ast
import importlib
import inspect
import sys
import textwrap
import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from honoka.app import App
from honoka.router import RouteInfo, path_params

_UNRESOLVED = object()


@dataclass(frozen=True, slots=True)
class Finding:
    route: RouteInfo | None
    message: str
    error: bool

    def render(self) -> str:
        mark = "✗" if self.error else "!"
        where = f"{self.route.method} {self.route.path}: " if self.route else ""
        return f"{mark} {where}{self.message}"


def load_app(target: str) -> App:
    """Import `module:attribute` (attribute defaults to `app`) with the cwd on the path."""
    module_name, _, attribute = target.partition(":")
    if "" not in sys.path:
        sys.path.insert(0, "")
    module = importlib.import_module(module_name)
    app = getattr(module, attribute or "app")
    if not isinstance(app, App):
        raise TypeError(f"{target} is not a honoka App")
    return app


def _validated_types(route: RouteInfo) -> list[object]:
    types: list[object] = []
    for mw in route.middleware:
        schemas = getattr(mw, "schemas", None)
        if isinstance(schemas, dict):
            types.extend(schemas.values())
    return types


def _covers(validated: object, wanted: object) -> bool:
    have = typing.get_origin(validated) or validated
    want = typing.get_origin(wanted) or wanted
    return isinstance(have, type) and isinstance(want, type) and issubclass(have, want)


def _resolve(node: ast.expr, handler: Callable[..., object]) -> object:
    """The runtime object an expression like `CreateUser` or `models.User` names, if knowable."""
    if isinstance(node, ast.Name):
        scope = cast("dict[str, object]", getattr(handler, "__globals__", {}))
        if node.id in scope:
            return scope[node.id]
        try:
            nonlocals: dict[str, object] = dict(inspect.getclosurevars(handler).nonlocals)
        except (TypeError, ValueError):
            return _UNRESOLVED
        return nonlocals.get(node.id, _UNRESOLVED)
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, handler)
        return _UNRESOLVED if base is _UNRESOLVED else getattr(base, node.attr, _UNRESOLVED)
    return _UNRESOLVED


def _type_name(tp: object) -> str:
    return str(getattr(tp, "__name__", tp))


def analyze(route: RouteInfo) -> list[Finding]:
    findings: list[Finding] = []
    handler = route.handler
    name = _type_name(handler)
    if inspect.signature(handler).return_annotation is inspect.Signature.empty:
        findings.append(Finding(route, f"{name} has no return annotation", error=False))
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
    except (OSError, TypeError, SyntaxError):
        return findings

    path_names = [name for name, _ in path_params(route.path)]
    read: set[str] = set()
    dynamic = False
    typos: list[str] = []
    wanted: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "params":
            dynamic = True
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "param":
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                read.add(first.value)
                if first.value not in path_names:
                    typos.append(first.value)
            else:
                dynamic = True
        elif node.func.attr == "valid" and node.args:
            wanted.append(node.args[0])

    for typo in typos:
        findings.append(
            Finding(
                route, f"c.req.param({typo!r}) is not a path parameter of this route", error=True
            )
        )
    has_param_validator = any(
        isinstance(getattr(mw, "schemas", None), dict) and "param" in getattr(mw, "schemas", {})
        for mw in route.middleware
    )
    if not typos and not dynamic and not has_param_validator:
        findings.extend(
            Finding(route, f"path parameter {n!r} is never read by {name}", error=True)
            for n in path_names
            if n not in read
        )

    validated = _validated_types(route)
    for expr in wanted:
        target = _resolve(expr, handler)
        if target is _UNRESOLVED or any(_covers(v, target) for v in validated):
            continue
        names = ", ".join(_type_name(v) for v in validated) or "nothing"
        findings.append(
            Finding(
                route,
                f"c.req.valid({_type_name(target)}) is called, but the route validates {names}",
                error=True,
            )
        )
    return findings


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _routes(app: App) -> int:
    table = app.route_table()
    method_width = max((len(r.method) for r in table), default=0)
    path_width = max((len(r.path) for r in table), default=0)
    for r in table:
        handler = _type_name(r.handler)
        print(f"{r.method:<{method_width}}  {r.path:<{path_width}}  {handler}")
    return 0


def _check(app: App) -> int:
    findings: list[Finding] = []
    try:
        app.finalize()
    except ValueError as exc:
        findings.append(Finding(None, str(exc), error=True))
    table = app.route_table()
    for route in table:
        findings.extend(analyze(route))
    for finding in findings:
        print(finding.render())
    errors = sum(f.error for f in findings)
    warnings = len(findings) - errors
    if not findings:
        print(f"ok: {_plural(len(table), 'route')} checked")
    else:
        print(f"{_plural(errors, 'error')}, {_plural(warnings, 'warning')}")
    return 1 if errors else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="honoka")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("routes", "list the routes"), ("check", "find mistakes in the routes")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("app", help="module:attribute of the App, e.g. main:app")
    args = parser.parse_args(argv)
    try:
        app = load_app(args.app)
    except (ImportError, AttributeError, TypeError) as exc:
        print(f"honoka: cannot import {args.app}: {exc}", file=sys.stderr)
        return 2
    return _routes(app) if args.command == "routes" else _check(app)


def run() -> None:
    sys.exit(main())
