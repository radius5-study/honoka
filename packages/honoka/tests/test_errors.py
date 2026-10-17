"""Errors become responses at the layer that raised them, so outer middleware still sees one."""

from honoka import App, Context, HTTPError, Next, Response


async def stamp_status(c: Context, call_next: Next) -> Response:
    response = await call_next()
    response.headers.append(("x-seen-status", str(response.status)))
    return response


def test_http_error_becomes_its_status_with_a_structured_body_and_headers() -> None:
    app = App()

    def user(c: Context) -> Response:
        raise HTTPError(404, "user_not_found", "no user 7", headers=[("x-hint", "check id")])

    app.get("/users/{user_id}", user)

    with app.test() as client:
        resp = client.get("/users/7")

    assert resp.status == 404
    assert resp.json() == {"error": "user_not_found", "detail": "no user 7"}
    assert resp.headers["x-hint"] == "check id"


def test_an_unhandled_exception_is_a_500_that_does_not_leak_details() -> None:
    app = App()

    async def boom(c: Context) -> Response:
        raise RuntimeError("secret database password")

    app.get("/boom", boom)

    with app.test() as client:
        resp = client.get("/boom")

    assert resp.status == 500
    assert resp.json() == {"error": "internal_error"}


def test_outer_middleware_sees_the_error_response_instead_of_an_exception() -> None:
    app = App()
    app.use("*", stamp_status)

    def boom(c: Context) -> Response:
        raise RuntimeError("x")

    app.get("/boom", boom)

    def missing(c: Context) -> Response:
        raise HTTPError(410, "gone")

    app.get("/missing", missing)

    with app.test() as client:
        assert client.get("/boom").headers["x-seen-status"] == "500"
        assert client.get("/missing").headers["x-seen-status"] == "410"


def test_an_http_error_raised_by_middleware_is_handled_too() -> None:
    app = App()

    async def deny(c: Context, call_next: Next) -> Response:
        raise HTTPError(403, "forbidden")

    app.use("/private/*", deny)

    def private(c: Context) -> Response:
        return c.text("a")

    app.get("/private/a", private)

    with app.test() as client:
        resp = client.get("/private/a")

    assert (resp.status, resp.json()) == (403, {"error": "forbidden"})


def test_on_error_receives_the_error_and_decides_the_response() -> None:
    app = App()

    @app.on_error
    def on_error(c: Context) -> Response:
        return c.json({"oops": type(c.error).__name__}, 418)

    def boom(c: Context) -> Response:
        raise ValueError("x")

    app.get("/boom", boom)

    with app.test() as client:
        resp = client.get("/boom")

    assert (resp.status, resp.json()) == (418, {"oops": "ValueError"})


def test_an_async_on_error_is_awaited() -> None:
    app = App()

    @app.on_error
    async def on_error(c: Context) -> Response:
        return c.text("handled", 500)

    def boom(c: Context) -> Response:
        raise ValueError("x")

    app.get("/boom", boom)

    with app.test() as client:
        assert client.get("/boom").text == "handled"


def test_an_on_error_that_fails_falls_back_to_the_default_500() -> None:
    app = App()

    @app.on_error
    def on_error(c: Context) -> Response:
        raise RuntimeError("the handler is broken too")

    def boom(c: Context) -> Response:
        raise ValueError("x")

    app.get("/boom", boom)

    with app.test() as client:
        resp = client.get("/boom")

    assert (resp.status, resp.json()) == (500, {"error": "internal_error"})


def test_not_found_handler_answers_unmatched_paths() -> None:
    app = App()

    @app.not_found
    def not_found(c: Context) -> Response:
        return c.text(f"no {c.req.path}", 404)

    with app.test() as client:
        resp = client.get("/nowhere")

    assert (resp.status, resp.text) == (404, "no /nowhere")


def test_middleware_wraps_unmatched_requests_too() -> None:
    app = App()
    app.use("*", stamp_status)

    with app.test() as client:
        assert client.get("/nowhere").headers["x-seen-status"] == "404"


def test_middleware_can_answer_a_preflight_for_a_path_without_that_method() -> None:
    app = App()

    async def preflight(c: Context, call_next: Next) -> Response:
        if c.req.method == "OPTIONS":
            return Response(b"", 204, [("x-preflight", "1")])
        return await call_next()

    app.use("/api/*", preflight)

    with app.test() as client:
        resp = client.request("OPTIONS", "/api/anything")

    assert (resp.status, resp.headers["x-preflight"]) == (204, "1")
