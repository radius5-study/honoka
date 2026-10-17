"""`app.get(path, *middleware, handler)`: Hono's one-expression registration, the only form."""

from typing import cast

import pytest

from honoka import App, Context, Handler, Middleware, Next, Response


async def deny(c: Context, call_next: Next) -> Response:
    return c.text("denied", 403)


async def stamp(c: Context, call_next: Next) -> Response:
    response = await call_next()
    response.headers.append(("x-stamp", "1"))
    return response


def test_a_handler_given_directly_is_registered() -> None:
    app = App()
    app.get("/health", lambda c: c.text("ok"))
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


def test_other_verbs_on_and_all_take_a_handler_too() -> None:
    app = App()

    async def create(c: Context) -> Response:
        return c.text("created", 201)

    app.get("/a", lambda c: c.text("a"))
    app.post("/a", create)
    app.on(["PUT", "PATCH"], "/b", lambda c: c.text(c.req.method))
    app.all("/c", lambda c: c.text("c"))
    with app.test() as client:
        seen = [
            client.get("/a").text,
            client.post("/a").status,
            client.put("/b").text,
            client.patch("/b").text,
            client.delete("/c").text,
        ]
    assert seen == ["a", 201, "PUT", "PATCH", "c"]


def test_a_call_with_middleware_but_no_handler_is_refused() -> None:
    """The old `@app.get(path, auth)` decorator form is gone; forgetting the handler must fail
    loudly at registration, not register `auth` as the handler."""
    app = App()
    with pytest.raises(TypeError, match="handler"):
        app.get("/x", cast(Handler, deny))
    nothing = cast(tuple[Handler], ())  # the checker wants a handler; pass none at runtime
    with pytest.raises(TypeError, match="handler"):
        app.get("/x", *nothing)


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
