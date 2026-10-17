"""some, every, except_."""

import pytest
from honoka_combine import every, except_, some

from honoka import App, Context, HTTPError, Middleware, Next, Response


def needs(header: str) -> Middleware:
    async def mw(c: Context, call_next: Next) -> Response:
        if c.req.header(header) is None:
            raise HTTPError(401, header)
        return await call_next()

    return mw


def tracing(name: str, log: list[str]) -> Middleware:
    async def mw(c: Context, call_next: Next) -> Response:
        log.append(name)
        return await call_next()

    return mw


def build(mw: Middleware) -> App:
    app = App()
    app.use("*", mw)

    def root(c: Context) -> Response:
        return c.text("ok")

    app.get("/", root)

    def public(c: Context) -> Response:
        return c.text("public")

    app.get("/api/public/x", public)

    return app


@pytest.mark.parametrize(
    ("headers", "status"),
    [({"a": "1"}, 200), ({"b": "1"}, 200), ({}, 401)],
)
def test_some_passes_when_any_passes(headers: dict[str, str], status: int) -> None:
    with build(some(needs("a"), needs("b"))).test() as client:
        assert client.get("/", headers=headers).status == status


def test_some_stops_at_first_pass() -> None:
    log: list[str] = []
    with build(some(tracing("one", log), tracing("two", log))).test() as client:
        assert client.get("/").text == "ok"
    assert log == ["one"]


def test_some_treats_an_answer_without_next_as_failure() -> None:
    async def deny(c: Context, call_next: Next) -> Response:
        return c.text("denied", 403)

    with build(some(deny)).test() as client:
        resp = client.get("/")
    assert (resp.status, resp.text) == (403, "denied")
    with build(some(deny, needs("a"))).test() as client:
        assert client.get("/", headers={"a": "1"}).text == "ok"


def test_every_needs_all_and_runs_in_order() -> None:
    log: list[str] = []
    mw = every(tracing("one", log), needs("a"), tracing("two", log), needs("b"))
    with build(mw).test() as client:
        assert client.get("/", headers={"a": "1"}).status == 401
        assert log == ["one", "two"]
        log.clear()
        assert client.get("/", headers={"a": "1", "b": "1"}).text == "ok"
        assert log == ["one", "two"]
        assert client.get("/").status == 401


def test_nested_some_of_every() -> None:
    mw = some(every(needs("a"), needs("b")), needs("c"))
    with build(mw).test() as client:
        assert client.get("/", headers={"a": "1"}).status == 401
        assert client.get("/", headers={"a": "1", "b": "1"}).status == 200
        assert client.get("/", headers={"c": "1"}).status == 200


def test_except_skips_for_patterns_and_predicates() -> None:
    mw = except_(["/api/public/*", "/open"], needs("a"))
    with build(mw).test() as client:
        assert client.get("/api/public/x").text == "public"
        assert client.get("/").status == 401
    by_predicate = except_(lambda c: c.req.header("x-skip") is not None, needs("a"))
    with build(by_predicate).test() as client:
        assert client.get("/", headers={"x-skip": "1"}).status == 200
        assert client.get("/").status == 401
    exact = except_("/", needs("a"))
    with build(exact).test() as client:
        assert client.get("/").status == 200
        assert client.get("/api/public/x").status == 401


def test_except_patterns_follow_the_routing_rules_and_a_bad_one_fails_at_registration() -> None:
    app = App()
    app.use("*", except_("/users/{id:int}/public", needs("a")))
    app.get("/users/{id}/public", lambda c: c.text("public"))
    with app.test() as client:
        assert client.get("/users/7/public").status == 200  # skipped: a route-style pattern
        assert client.get("/users/x/public").status == 401  # {id:int} rejects "x", so not skipped
    with pytest.raises(ValueError, match="pattern"):
        except_("/users/{id", needs("a"))
