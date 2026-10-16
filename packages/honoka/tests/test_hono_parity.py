"""Hono's remaining app, request, cookie and streaming APIs, through the public test client."""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest

from honoka import (
    App,
    Context,
    HTTPError,
    Next,
    Response,
    SSEEvent,
    UploadedFile,
    WebSocket,
    generate_cookie,
)
from honoka.asgi import AsgiScope, Receive, Send


def test_base_path_prefixes_routes_middleware_and_fallbacks() -> None:
    app = App()
    api = app.base_path("/api")

    async def stamp(c: Context, call_next: Next) -> Response:
        response = await call_next()
        response.headers.append(("x-api", "yes"))
        return response

    api.use("*", stamp)

    @api.get("/book")
    def book(c: Context) -> Response:
        return c.text(c.req.path)

    @api.not_found
    def missing(c: Context) -> Response:
        return c.text("api missing", 404)

    @app.get("/other")
    def other(c: Context) -> Response:
        return c.text("other")

    with app.test() as client:
        got = client.get("/api/book")
        assert (got.text, got.headers["x-api"]) == ("/api/book", "yes")
        assert client.get("/book").status == 404
        assert client.get("/api/nope").text == "api missing"
        assert "x-api" not in client.get("/other").headers


def test_the_query_method_and_custom_methods_and_several_paths() -> None:
    app = App()

    @app.query("/search")
    def search(c: Context) -> Response:
        return c.text("queried")

    @app.on("PURGE", "/cache")
    def purge(c: Context) -> Response:
        return c.text("purged")

    @app.on("GET", ["/hello", "/ja/hello"])
    def hello(c: Context) -> Response:
        return c.text("hi")

    with app.test() as client:
        assert client.request("QUERY", "/search").text == "queried"
        assert client.request("PURGE", "/cache").text == "purged"
        assert [client.get(p).text for p in ("/hello", "/ja/hello")] == ["hi", "hi"]


def test_get_path_decides_what_is_routed() -> None:
    app = App(get_path=lambda scope: "/" + scope["path"].lstrip("/").upper())

    @app.get("/HELLO")
    def hello(c: Context) -> Response:
        return c.text(c.req.path)

    with app.test() as client:
        assert client.get("/hello").text == "/HELLO"


def test_a_mounted_asgi_app_sees_the_path_below_the_prefix() -> None:
    async def inner(scope: AsgiScope, receive: Receive, send: Send) -> None:
        body = f"{scope['path']}|{scope['root_path']}".encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})

    app = App()
    app.mount("/legacy", inner)

    @app.get("/new")
    def new(c: Context) -> Response:
        return c.text("new")

    with app.test() as client:
        assert client.get("/legacy/a/b").text == "/a/b|/legacy"
        assert client.get("/legacy").text == "/|/legacy"
        assert client.get("/legacyx").status == 404
        assert client.get("/new").text == "new"


def test_a_websocket_handler_talks_to_a_client() -> None:
    app = App()

    @app.ws("/echo/{room}")
    async def echo(ws: WebSocket) -> None:
        await ws.accept()
        await ws.send_text(f"joined {ws.params['room']}")
        async for message in ws:
            await ws.send_json({"echo": message})

    with app.test() as client, client.websocket("/echo/lobby") as ws:
        assert ws.accepted
        assert ws.receive_text() == "joined lobby"
        ws.send_text("hi")
        assert ws.receive_json() == {"echo": "hi"}


def test_a_websocket_to_an_unknown_path_is_refused() -> None:
    with App().test() as client, client.websocket("/nope") as ws:
        assert not ws.accepted
        assert ws.close_code == 1000


def test_request_header_url_text_and_all_cookies() -> None:
    app = App()

    @app.post("/info")
    async def info(c: Context) -> Response:
        return c.json(
            {
                "headers": c.req.header(),
                "url": c.req.url,
                "text": await c.req.text(),
                "cookies": c.req.cookie(),
            }
        )

    with app.test() as client:
        got = client.post(
            "/info?x=1", headers={"host": "example.test", "cookie": "a=1; b=2"}, content=b"hello"
        ).json()
    assert isinstance(got, dict)
    assert got["url"] == "http://example.test/info?x=1"
    assert (got["text"], got["cookies"]) == ("hello", {"a": "1", "b": "2"})
    assert got["headers"]["host"] == "example.test"


def test_parse_body_reads_urlencoded_and_multipart_forms() -> None:
    app = App()

    @app.post("/form")
    async def form(c: Context) -> Response:
        body = await c.req.parse_body(all=True)
        shown = {
            k: (
                f"{v.filename}:{v.content_type}:{v.data.decode()}"
                if isinstance(v, UploadedFile)
                else v
            )
            for k, v in body.items()
        }
        return c.json(shown)

    boundary = "XBOUNDARY"
    multipart = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\nhello\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\nfile body\r\n--{boundary}--\r\n"
    ).encode()
    with app.test() as client:
        urlencoded = client.post("/form", form={"a": "1"}).json()
        repeated = client.post(
            "/form",
            headers={"content-type": "application/x-www-form-urlencoded"},
            content=b"t=1&t=2&only=3",
        ).json()
        multi = client.post(
            "/form",
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            content=multipart,
        ).json()
        other = client.post("/form", json={"a": 1}).json()
    assert urlencoded == {"a": "1"}
    assert repeated == {"t": ["1", "2"], "only": "3"}
    assert multi == {"title": "hello", "file": "a.txt:text/plain:file body"}
    assert other == {}


def test_parse_body_dot_nests_keys() -> None:
    app = App()

    @app.post("/form")
    async def form(c: Context) -> Response:
        return c.json(await c.req.parse_body(dot=True))

    with app.test() as client:
        assert client.post("/form", form={"user.name": "a", "user.id": "7"}).json() == {
            "user": {"name": "a", "id": "7"}
        }


def test_cookie_options_prefixes_and_deletion_return_value() -> None:
    app = App()
    when = datetime.now(UTC) + timedelta(days=1)

    @app.get("/set")
    def set_cookies(c: Context) -> Response:
        c.cookie("p", "1", prefix="secure", priority="High", partitioned=True, expires=when)
        c.cookie("h", "2", prefix="host")
        return c.text("")

    @app.get("/del")
    def delete(c: Context) -> Response:
        return c.text(str(c.delete_cookie("sid")))

    @app.get("/read")
    def read(c: Context) -> Response:
        return c.text(f"{c.req.cookie('p', prefix='secure')}|{c.req.cookie('p')}")

    with app.test() as client:
        raw = [v for k, v in client.get("/set").raw_headers if k == "set-cookie"]
        assert raw[0].startswith("__Secure-p=1; Path=/; Expires=")
        assert all(part in raw[0] for part in ("Secure", "Priority=High", "Partitioned"))
        assert raw[1].startswith("__Host-h=2; Path=/; Secure")
        assert client.get("/del", headers={"cookie": "sid=abc"}).text == "abc"
        assert client.get("/read", headers={"cookie": "__Secure-p=7"}).text == "7|None"


@pytest.mark.parametrize(
    "make",
    [
        lambda: generate_cookie("n", "v", prefix="host", path="/x"),
        lambda: generate_cookie("n", "v", prefix="host", domain="example.test"),
        lambda: generate_cookie("n", "v", max_age=400 * 24 * 3600 + 1),
        lambda: generate_cookie("n", "v", expires=datetime.now(UTC) + timedelta(days=401)),
    ],
    ids=["host-path", "host-domain", "max-age", "expires"],
)
def test_cookie_rules_hono_enforces_are_enforced(make: Callable[[], str]) -> None:
    with pytest.raises(ValueError, match="Host|400"):
        make()


def test_signed_cookies_verify_and_detect_tampering() -> None:
    app = App()

    @app.get("/set")
    def set_it(c: Context) -> Response:
        c.signed_cookie("sid", "abc", "s3cret")
        return c.text("")

    @app.get("/get")
    def get_it(c: Context) -> Response:
        return c.text(repr(c.req.signed_cookie("sid", "s3cret")))

    with app.test() as client:
        header = next(v for k, v in client.get("/set").raw_headers if k == "set-cookie")
        pair = header.split(";")[0]
        assert client.get("/get", headers={"cookie": pair}).text == "'abc'"
        assert client.get("/get", headers={"cookie": pair + "x"}).text == "False"
        assert client.get("/get", headers={"cookie": "sid=abc"}).text == "False"
        assert client.get("/get", headers={"cookie": "sid=abc.é"}).text == "False"
        assert client.get("/get").text == "None"


def test_server_sent_events_are_framed() -> None:
    app = App()

    @app.get("/events")
    def events(c: Context) -> Response:
        async def source() -> AsyncIterator[str | SSEEvent]:
            yield "plain"
            yield SSEEvent("two\nlines", event="tick", id="7", retry=1000)

        return c.sse(source())

    with app.test() as client:
        resp = client.get("/events")
    assert resp.headers["content-type"] == "text/event-stream"
    assert resp.text == (
        "data: plain\n\nevent: tick\nid: 7\nretry: 1000\ndata: two\ndata: lines\n\n"
    )


def test_an_sse_event_name_cannot_inject_fields() -> None:
    app = App()

    @app.get("/events")
    def events(c: Context) -> Response:
        async def source() -> AsyncIterator[SSEEvent]:
            yield SSEEvent("x", event="a\ndata: forged")

        return c.sse(source())

    with app.test() as client, pytest.raises(ValueError, match="line breaks"):
        client.get("/events")


def test_stream_text_sends_chunks() -> None:
    app = App()

    @app.get("/t")
    def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str]:
            for part in ("a", "b"):
                yield part

        return c.stream_text(chunks())

    with app.test() as client:
        assert client.get("/t").text == "ab"


def test_http_error_can_carry_its_own_response() -> None:
    app = App()

    @app.get("/auth")
    def auth(c: Context) -> Response:
        raise HTTPError(
            401,
            "unauthorized",
            response=Response(b"nope", 200, [("www-authenticate", 'Bearer realm="x"')]),
        )

    @app.get("/plain")
    def plain(c: Context) -> Response:
        raise HTTPError(409, "conflict")

    with app.test() as client:
        carried = client.get("/auth")
        built = client.get("/plain")
    assert (carried.status, carried.text, carried.headers["www-authenticate"]) == (
        401,
        "nope",
        'Bearer realm="x"',
    )
    assert (built.status, built.json()) == (409, {"error": "conflict"})


def test_the_raw_asgi_scope_is_available_to_adapters() -> None:
    app = App()

    @app.get("/scope")
    def scope(c: Context) -> Response:
        return c.text(str(c.req.scope["server"]))

    with app.test() as client:
        assert client.get("/scope").text == "('testserver', 80)"
