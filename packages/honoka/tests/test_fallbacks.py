"""`on_error` and `not_found` as Hono v5 routable, stacking middleware, and `c.not_found()`."""

from honoka import App, Context, HTTPError, Next, Response


def test_not_found_steps_stack_and_a_step_can_hand_over_with_next() -> None:
    app = App()

    @app.not_found
    async def mark(c: Context, call_next: Next) -> Response:
        c.header("x-seen", "mark")
        return await call_next()

    @app.not_found
    def answer(c: Context) -> Response:
        return c.text("custom", 404)

    with app.test() as client:
        resp = client.get("/nope")
    assert (resp.status, resp.text, resp.headers["x-seen"]) == (404, "custom", "mark")


def test_a_path_scoped_not_found_only_covers_that_path() -> None:
    app = App()
    app.not_found("/api/*", lambda c: c.json({"error": "api_missing"}, 404))

    with app.test() as client:
        assert client.get("/api/x").json() == {"error": "api_missing"}
        assert client.get("/web/x").json() == {"error": "not_found"}


def test_when_every_step_hands_over_the_built_in_404_answers() -> None:
    app = App()

    @app.not_found
    async def pass_on(c: Context, call_next: Next) -> Response:
        return await call_next()

    with app.test() as client:
        resp = client.get("/nope")
    assert (resp.status, resp.json()) == (404, {"error": "not_found"})


def test_c_not_found_runs_the_not_found_steps_from_inside_a_handler() -> None:
    app = App()

    async def user(c: Context) -> Response:
        c.not_found()

    app.get("/users/{id}", user)

    @app.not_found
    def custom(c: Context) -> Response:
        return c.text("no such user", 404)

    with app.test() as client:
        resp = client.get("/users/7")
    assert (resp.status, resp.text) == (404, "no such user")


def test_an_on_error_step_reads_c_error_and_may_hand_over_to_the_built_in_answer() -> None:
    app = App()

    def teapot(c: Context) -> Response:
        raise HTTPError(418, "teapot")

    app.get("/teapot", teapot)

    def boom(c: Context) -> Response:
        raise RuntimeError("secret")

    app.get("/boom", boom)

    @app.on_error
    async def only_runtime(c: Context, call_next: Next) -> Response:
        if isinstance(c.error, RuntimeError):
            return c.text("handled", 500)
        return await call_next()

    with app.test() as client:
        handled = client.get("/boom")
        built_in = client.get("/teapot")
    assert (handled.status, handled.text) == (500, "handled")
    assert (built_in.status, built_in.json()) == (418, {"error": "teapot"})


def test_on_error_registrations_are_scoped_by_path_and_stack() -> None:
    app = App()

    def api(c: Context) -> Response:
        raise RuntimeError

    app.get("/api/x", api)

    def web(c: Context) -> Response:
        raise RuntimeError

    app.get("/web/x", web)

    app.on_error("/api/*", lambda c: c.json({"where": "api"}, 500))

    with app.test() as client:
        assert client.get("/api/x").json() == {"where": "api"}
        assert client.get("/web/x").json() == {"error": "internal_error"}


def test_an_on_error_that_raises_is_answered_by_the_built_in_500() -> None:
    app = App()

    def boom(c: Context) -> Response:
        raise RuntimeError

    app.get("/boom", boom)

    @app.on_error
    def broken(c: Context) -> Response:
        raise ValueError

    with app.test() as client:
        resp = client.get("/boom")
    assert (resp.status, resp.json()) == (500, {"error": "internal_error"})


def test_a_mounted_sub_apps_fallbacks_run_before_the_parents_below_the_prefix() -> None:
    sub = App()

    @sub.not_found
    async def from_sub(c: Context, call_next: Next) -> Response:
        c.header("x-order", "sub")
        return await call_next()

    parent = App()
    parent.route("/v1", sub)

    @parent.not_found
    def from_parent(c: Context) -> Response:
        return c.text(c.req.path, 404)

    with parent.test() as client:
        inside = client.get("/v1/nope")
        outside = client.get("/nope")
    assert (inside.text, inside.headers["x-order"]) == ("/v1/nope", "sub")
    assert "x-order" not in outside.headers
