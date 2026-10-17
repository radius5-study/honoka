"""`app.get(path, handler)`: Hono's one-expression registration, next to the decorator form."""

from typing import cast

import pytest

from honoka import App, Context, Middleware, Next, Response


async def deny(c: Context, call_next: Next) -> Response:
    return c.text("denied", 403)


async def stamp(c: Context, call_next: Next) -> Response:
    response = await call_next()
    response.headers.append(("x-stamp", "1"))
    return response


def test_a_handler_given_directly_is_registered_and_the_app_is_returned() -> None:
    app = App()
    assert app.get("/health", lambda c: c.text("ok")) is app
    with app.test() as client:
        resp = client.get("/health")
    assert (resp.status, resp.text) == (200, "ok")


def test_middleware_before_the_handler_wraps_it() -> None:
    app = App()
    app.get("/open", stamp, lambda c: c.text("in"))
    app.get("/closed", deny, lambda c: c.text("never"))
    with app.test() as client:
        open_, closed = client.get("/open"), client.get("/closed")
    assert (open_.text, open_.headers["x-stamp"]) == ("in", "1")
    assert (closed.status, closed.text) == (403, "denied")


def test_calls_chain_and_cover_other_verbs_on_and_all() -> None:
    app = App()

    async def create(c: Context) -> Response:
        return c.text("created", 201)

    app.get("/a", lambda c: c.text("a")).post("/a", create).on(
        ["PUT", "PATCH"], "/b", lambda c: c.text(c.req.method)
    ).all("/c", lambda c: c.text("c"))
    with app.test() as client:
        seen = [
            client.get("/a").text,
            client.post("/a").status,
            client.put("/b").text,
            client.patch("/b").text,
            client.delete("/c").text,
        ]
    assert seen == ["a", 201, "PUT", "PATCH", "c"]


def test_the_decorator_form_still_works_and_returns_the_function() -> None:
    app = App()

    @app.get("/plain")
    def plain(c: Context) -> Response:
        return c.text("plain")

    @app.get("/guarded", deny)
    async def guarded(c: Context) -> Response:
        return c.text("never")

    assert plain.__name__ == "plain"  # the decorator hands the function back
    with app.test() as client:
        assert client.get("/plain").text == "plain"
        assert client.get("/guarded").status == 403


def test_two_handlers_are_refused_because_the_first_is_not_middleware() -> None:
    app = App()
    first = cast(
        Middleware, lambda c: c.text("1")
    )  # the checker rejects this call; test the runtime
    with pytest.raises(TypeError):
        app.get("/x", first, lambda c: c.text("2"))


def test_a_duplicate_route_registered_directly_is_refused() -> None:
    app = App()
    app.get("/x", lambda c: c.text("1"))
    with pytest.raises(ValueError, match="duplicate"):
        app.get("/x", lambda c: c.text("2"))
