"""SmartRouter: try RegExpRouter, then TrieRouter, and keep the first that takes every route."""

import re

import pytest

from honoka import (
    App,
    Context,
    RegExpRouter,
    Response,
    SmartRouter,
    TrieRouter,
    UnsupportedPathError,
)
from honoka import router as router_module
from honoka.router import Route, RouterFactory


def route(method: str, pattern: str) -> Route:
    return Route(method, pattern, lambda c: c.text(""), (), False, {})


def populated(smart: SmartRouter) -> SmartRouter:
    smart.add(route("GET", "/users/{id:int}"))
    smart.add(route("GET", "/health"))
    return smart


def test_it_keeps_the_regexp_router_when_that_can_take_the_routes() -> None:
    smart = populated(SmartRouter())
    smart.compile()
    assert smart.chosen == "RegExpRouter"
    match = smart.match("GET", "/users/7")
    assert match is not None
    assert dict(match[1]) == {"id": "7"}


def test_it_falls_back_to_the_trie_router_when_the_regexp_router_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(root: object) -> object:
        raise re.error("simulated: the pattern is too large")

    monkeypatch.setattr(router_module, "_regex_matcher", refuse)
    smart = populated(SmartRouter())
    smart.compile()
    assert smart.chosen == "TrieRouter"
    match = smart.match("GET", "/users/7")
    assert match is not None
    assert dict(match[1]) == {"id": "7"}


def test_a_mistake_in_the_routes_is_not_mistaken_for_an_unsupported_set() -> None:
    smart = SmartRouter()
    smart.add(route("GET", "/a/{x}"))
    smart.add(route("GET", "/a/{y}/b"))
    with pytest.raises(ValueError, match="conflicting parameter names"):
        smart.compile()


def test_the_order_of_the_candidates_is_the_order_of_preference() -> None:
    smart = populated(SmartRouter(routers=(TrieRouter, RegExpRouter)))
    smart.compile()
    assert smart.chosen == "TrieRouter"


def test_when_no_candidate_can_take_the_routes_it_is_a_fatal_error() -> None:
    class Refusing:
        name = "Refusing"

        def __init__(self, *, strict: bool = True) -> None: ...

        def add(self, route: Route) -> None: ...

        def compile(self) -> None:
            raise UnsupportedPathError("never")

        def match(self, method: str, path: str) -> None: ...

        def allowed_methods(self, path: str) -> list[str]:
            return []

    smart = populated(SmartRouter(routers=(Refusing, Refusing)))
    with pytest.raises(RuntimeError, match="Fatal error"):
        smart.compile()


def test_a_route_cannot_be_added_after_the_choice_is_made() -> None:
    smart = populated(SmartRouter())
    smart.compile()
    with pytest.raises(RuntimeError, match="after the router has been chosen"):
        smart.add(route("GET", "/late"))


@pytest.mark.parametrize("factory", [RegExpRouter, TrieRouter, SmartRouter])
def test_an_app_can_be_given_the_router_it_should_use(factory: RouterFactory) -> None:
    app = App(router=factory)

    def handler(c: Context) -> Response:
        return c.text(c.req.param("id"))

    app.get("/n/{id:int}", handler)

    with app.test() as client:
        assert client.get("/n/12").text == "12"
        assert client.get("/n/x").status == 404
