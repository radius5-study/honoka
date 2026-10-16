"""Response-side helpers: cookies and streaming, via the public test client."""

from collections.abc import AsyncIterator

from honoka import App, Context, Response


def test_set_and_delete_cookie_emit_one_set_cookie_header_each() -> None:
    app = App()

    @app.get("/")
    def handler(c: Context) -> Response:
        c.cookie("sid", "abc", max_age=60)
        c.cookie("theme", "dark", http_only=False, same_site="Strict", secure=True)
        c.delete_cookie("old")
        return c.text("ok")

    with app.test() as client:
        resp = client.get("/")

    cookies = [v for k, v in resp.raw_headers if k == "set-cookie"]
    assert cookies == [
        "sid=abc; Path=/; Max-Age=60; HttpOnly; SameSite=Lax",
        "theme=dark; Path=/; Secure; SameSite=Strict",
        "old=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
    ]


def test_cookies_set_before_an_error_still_reach_the_client() -> None:
    app = App()

    @app.get("/")
    def handler(c: Context) -> Response:
        c.cookie("sid", "abc")
        raise RuntimeError("boom")

    with app.test() as client:
        resp = client.get("/")

    assert resp.status == 500
    assert ("set-cookie", "sid=abc; Path=/; HttpOnly; SameSite=Lax") in resp.raw_headers


def test_stream_sends_chunks_in_order_with_the_given_content_type() -> None:
    app = App()

    @app.get("/stream")
    async def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str | bytes]:
            yield "one,"
            yield b"two,"
            yield "three"

        return c.stream(chunks(), content_type="text/csv")

    with app.test() as client:
        resp = client.get("/stream")

    assert resp.status == 200
    assert resp.text == "one,two,three"
    assert resp.headers["content-type"] == "text/csv"


def test_a_cookie_value_that_could_inject_a_header_is_rejected() -> None:
    app = App()

    @app.get("/")
    def handler(c: Context) -> Response:
        try:
            c.cookie("sid", "x\r\nset-cookie: evil=1")
        except ValueError:
            return c.text("rejected")
        return c.text("accepted")

    with app.test() as client:
        resp = client.get("/")

    assert resp.text == "rejected"
    assert not [v for k, v in resp.raw_headers if k == "set-cookie"]


def test_cookie_attributes_that_could_inject_a_header_or_attribute_are_rejected() -> None:
    app = App()

    @app.get("/")
    def handler(c: Context) -> Response:
        rejected = 0
        for kwargs in ({"path": "/; Domain=evil.test"}, {"domain": "a.test\r\nx: y"}):
            try:
                c.cookie("sid", "v", **kwargs)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            except ValueError:
                rejected += 1
        return c.text(str(rejected))

    with app.test() as client:
        assert client.get("/").text == "2"


def test_head_on_a_streaming_route_sends_headers_but_no_chunks() -> None:
    app = App()

    @app.get("/stream")
    async def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str | bytes]:
            yield "data"

        return c.stream(chunks(), content_type="text/plain")

    with app.test() as client:
        resp = client.request("HEAD", "/stream")

    assert (resp.status, resp.body) == (200, b"")
    assert resp.headers["content-type"] == "text/plain"
