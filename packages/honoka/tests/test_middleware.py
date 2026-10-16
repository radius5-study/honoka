"""Middleware, typed keys across layers, and error handling, via the public test client."""

import pytest

from honoka import App, Context, Key, Middleware, Next, Response

USER: Key[str] = Key("user")


def tracer(name: str, log: list[str]) -> Middleware:
    async def middleware(c: Context, call_next: Next) -> Response:
        log.append(f"{name} in")
        response = await call_next()
        log.append(f"{name} out")
        return response

    return middleware


async def auth(c: Context, call_next: Next) -> Response:
    if c.req.header("authorization") is None:
        return c.text("denied", 401)
    c.set(USER, "alice")
    return await call_next()


def test_middleware_wraps_the_handler_in_registration_order() -> None:
    app = App()
    log: list[str] = []
    app.use("*", tracer("a", log))
    app.use("*", tracer("b", log))

    @app.get("/")
    def handler(c: Context) -> Response:
        log.append("handler")
        return c.text("")

    with app.test() as client:
        client.get("/")

    assert log == ["a in", "b in", "handler", "b out", "a out"]


def test_a_value_set_by_middleware_is_readable_by_the_handler() -> None:
    app = App()
    app.use("/api/*", auth)

    @app.get("/api/me")
    def me(c: Context) -> Response:
        return c.text(c.get(USER))

    with app.test() as client:
        assert client.get("/api/me", headers={"authorization": "t"}).text == "alice"


def test_middleware_can_answer_without_calling_the_handler() -> None:
    app = App()
    app.use("/api/*", auth)
    called: list[str] = []

    @app.get("/api/me")
    def me(c: Context) -> Response:
        called.append("handler")
        return c.text("me")

    with app.test() as client:
        resp = client.get("/api/me")

    assert (resp.status, resp.text) == (401, "denied")
    assert called == []


def test_a_path_pattern_limits_which_routes_a_middleware_wraps() -> None:
    app = App()
    app.use("/api/*", auth)

    @app.get("/api/me")
    def private(c: Context) -> Response:
        return c.text("private")

    @app.get("/public")
    def public(c: Context) -> Response:
        return c.text("public")

    with app.test() as client:
        assert client.get("/public").text == "public"
        assert client.get("/api/me").status == 401


def test_middleware_given_to_a_route_wraps_only_that_route() -> None:
    app = App()

    @app.get("/admin", auth)
    def admin(c: Context) -> Response:
        return c.text("admin")

    @app.get("/open")
    def open_(c: Context) -> Response:
        return c.text("open")

    with app.test() as client:
        assert client.get("/admin").status == 401
        assert client.get("/open").status == 200


def test_middleware_sees_the_matched_route_template() -> None:
    app = App()
    seen: list[str | None] = []

    async def spy(c: Context, call_next: Next) -> Response:
        seen.append(c.route)
        return await call_next()

    app.use("*", spy)

    @app.get("/users/{user_id}")
    def user(c: Context) -> Response:
        return c.text("")

    with app.test() as client:
        client.get("/users/7")

    assert seen == ["/users/{user_id}"]


def test_a_sync_function_is_rejected_as_middleware() -> None:
    app = App()

    def not_async(c: Context, call_next: Next) -> Response:
        return c.text("")

    with pytest.raises(TypeError, match="must be an async function"):
        app.use("*", not_async)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
