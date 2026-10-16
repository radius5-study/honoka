"""The response-building half of Context: header, status, body and the headers arguments."""

from honoka import App, Context, Next, Response


def test_c_header_sets_replaces_appends_and_removes_on_the_final_response() -> None:
    app = App()

    @app.get("/h")
    def handler(c: Context) -> Response:
        c.header("x-one", "1")
        c.header("x-one", "2")
        c.header("x-many", "a")
        c.header("x-many", "b", append=True)
        c.header("x-gone", None)
        return c.json({}, headers={"x-gone": "was here"})

    with app.test() as client:
        resp = client.get("/h")
    pairs = resp.raw_headers
    assert [v for k, v in pairs if k == "x-one"] == ["2"]
    assert [v for k, v in pairs if k == "x-many"] == ["a", "b"]
    assert "x-gone" not in resp.headers
    assert resp.headers["content-type"] == "application/json"


def test_c_header_in_a_middleware_after_next_still_reaches_the_response() -> None:
    app = App()

    async def stamp(c: Context, call_next: Next) -> Response:
        response = await call_next()
        c.header("x-after", "yes")
        return response

    app.use("*", stamp)

    @app.get("/")
    def home(c: Context) -> Response:
        return c.text("hi")

    with app.test() as client:
        assert client.get("/").headers["x-after"] == "yes"


def test_c_status_is_the_default_and_an_explicit_status_wins() -> None:
    app = App()

    @app.get("/default")
    def default(c: Context) -> Response:
        c.status(201)
        return c.text("made")

    @app.get("/explicit")
    def explicit(c: Context) -> Response:
        c.status(201)
        return c.json({}, 202)

    with app.test() as client:
        assert client.get("/default").status == 201
        assert client.get("/explicit").status == 202


def test_c_body_sends_raw_bytes_and_text_html_take_headers() -> None:
    app = App()

    @app.get("/raw")
    def raw(c: Context) -> Response:
        return c.body(b"\x00\x01", 206, {"content-type": "application/x-thing"})

    @app.get("/page")
    def page(c: Context) -> Response:
        return c.html("<p>x</p>", headers={"cache-control": "no-store"})

    with app.test() as client:
        raw_resp = client.get("/raw")
        page_resp = client.get("/page")
    assert (raw_resp.status, raw_resp.body, raw_resp.headers["content-type"]) == (
        206,
        b"\x00\x01",
        "application/x-thing",
    )
    assert page_resp.headers["cache-control"] == "no-store"
    assert page_resp.headers["content-type"] == "text/html; charset=utf-8"


def test_query_without_a_name_returns_every_parameter() -> None:
    app = App()

    @app.get("/q")
    def q(c: Context) -> Response:
        return c.json({"first": c.req.query(), "all": c.req.queries()})

    with app.test() as client:
        got = client.get("/q?a=1&a=2&b=3").json()
    assert got == {"first": {"a": "1", "b": "3"}, "all": {"a": ["1", "2"], "b": ["3"]}}
