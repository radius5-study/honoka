"""One app, two server interfaces: RSGI (Granian) and ASGI (uvicorn, Cloudflare Workers, ...).

The same requests must produce the same responses on both, byte for byte."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest

from honoka import App, Context, HTTPError, Json, Key, Next, Response
from honoka.asgi import AsgiMessage

REQUEST_ID: Key[str] = Key("rid")


def build(tmp_path: Path) -> tuple[App, list[str]]:
    app = App()
    log: list[str] = []
    (tmp_path / "note.txt").write_text("from disk")

    @app.on_startup
    def up() -> None:
        log.append("up")

    @app.on_shutdown
    def down() -> None:
        log.append("down")

    async def stamp(c: Context, call_next: Next) -> Response:
        c.set(REQUEST_ID, "r1")
        response = await call_next()
        response.headers.append(("x-request-id", c.get(REQUEST_ID)))
        return response

    app.use("*", stamp)

    @app.get("/text", inline=True)
    def text(c: Context) -> Response:
        c.cookie("a", "1", max_age=5)
        c.cookie("b", "2")
        return c.text("héllo")

    @app.get("/users/{user_id:int}")
    async def user(c: Context) -> Json[dict[str, object]]:
        return c.json(
            {
                "id": c.req.param("user_id", int),
                "q": c.req.query("q"),
                "tags": c.req.queries("tag"),
                "agent": c.req.header("user-agent"),
                "cookie": c.req.cookie("sid"),
            }
        )

    @app.post("/echo")
    async def echo(c: Context) -> Response:
        return c.json({"body": await c.req.json()}, 201)

    @app.get("/boom")
    def boom(c: Context) -> Response:
        raise HTTPError(418, "teapot", "short and stout")

    @app.get("/crash")
    def crash(c: Context) -> Response:
        raise RuntimeError("secret")

    @app.get("/stream")
    async def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str]:
            for part in ("one", "two", "three"):
                yield part

        return c.stream(chunks(), content_type="text/plain")

    @app.get("/file")
    def file(c: Context) -> Response:
        return c.file(tmp_path / "note.txt", filename="n.txt")

    @app.get("/jump")
    def jump(c: Context) -> Response:
        return c.redirect("/日本語")

    @app.get("/who")
    def who(c: Context) -> Response:
        return c.text(f"{c.req.cookie('a')}|{c.req.cookie('b')}|{c.req.header('x-multi')}")

    @app.get("/files/*")
    def wild(c: Context) -> Response:
        return c.text(c.req.path)

    @app.get("/log")
    def show(c: Context) -> Response:
        return c.text(",".join(log))

    return app, log


type Request = tuple[str, str, dict[str, str], bytes]

REQUESTS: list[Request] = [
    ("GET", "/text", {}, b""),
    ("GET", "/users/7?q=hi%20there&tag=a&tag=b", {"user-agent": "t", "cookie": "sid=s1"}, b""),
    ("POST", "/echo", {"content-type": "application/json"}, b'{"a":[1,2,{"b":null}]}'),
    ("GET", "/boom", {}, b""),
    ("GET", "/crash", {}, b""),
    ("GET", "/missing", {}, b""),
    ("DELETE", "/text", {}, b""),
    ("OPTIONS", "/text", {}, b""),
    ("HEAD", "/text", {}, b""),
    ("GET", "/stream", {}, b""),
    ("HEAD", "/stream", {}, b""),
    ("GET", "/file", {}, b""),
    ("GET", "/files/a/b.txt", {}, b""),
    ("GET", "/users/abc", {}, b""),
    ("GET", "/jump", {}, b""),
]


def outcome(app: App, interface: Literal["rsgi", "asgi"], request: Request) -> object:
    method, path, headers, body = request
    with app.test(interface=interface) as client:
        resp = client.request(method, path, headers=headers, content=body)
    return resp.status, sorted(resp.raw_headers), resp.body


@pytest.mark.parametrize("request_", REQUESTS, ids=[f"{m} {p}" for m, p, _, _ in REQUESTS])
def test_rsgi_and_asgi_answer_every_request_identically(request_: Request, tmp_path: Path) -> None:
    assert outcome(build(tmp_path)[0], "asgi", request_) == outcome(
        build(tmp_path)[0], "rsgi", request_
    )


def test_the_asgi_answers_are_the_real_thing_not_just_equal_to_each_other(tmp_path: Path) -> None:
    with build(tmp_path)[0].test(interface="asgi") as client:
        text = client.get("/text")
        crash = client.get("/crash")
        stream = client.get("/stream")
    assert (text.status, text.text) == (200, "héllo")
    assert [v for k, v in text.raw_headers if k == "set-cookie"] == [
        "a=1; Path=/; Max-Age=5; HttpOnly; SameSite=Lax",
        "b=2; Path=/; HttpOnly; SameSite=Lax",
    ]
    assert text.headers["x-request-id"] == "r1"
    assert (crash.status, crash.json()) == (500, {"error": "internal_error"})
    assert stream.text == "onetwothree"


def test_a_header_split_into_several_fields_is_read_as_one(tmp_path: Path) -> None:
    """HTTP/2 clients may send `cookie` as separate fields; RSGI hands them over joined."""
    app, _ = build(tmp_path)
    fields = [(b"cookie", b"a=1"), (b"cookie", b"b=2"), (b"x-multi", b"p"), (b"x-multi", b"q")]
    sent: list[AsgiMessage] = []

    async def receive() -> AsgiMessage:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: AsgiMessage) -> None:
        sent.append(message)

    scope: AsgiMessage = {"type": "http", "method": "GET", "path": "/who", "headers": fields}
    asyncio.run(app(scope, receive, send))
    assert sent[-1]["body"] == b"1|2|p, q"


def test_lifespan_runs_startup_and_shutdown_hooks_through_the_asgi_protocol(
    tmp_path: Path,
) -> None:
    app, log = build(tmp_path)
    with app.test(interface="asgi") as client:
        assert client.get("/log").text == "up"
        assert log == ["up"]
    assert log == ["up", "down"]


def test_without_a_lifespan_the_first_request_starts_the_app_exactly_once(tmp_path: Path) -> None:
    app, _ = build(tmp_path)

    async def hit() -> list[bytes]:
        from honoka.testing import asgi_call

        results = await asyncio.gather(*(asgi_call(app, "GET", "/log") for _ in range(5)))
        return [r.body for r in results]

    assert set(asyncio.run(hit())) == {b"up"}


def test_a_failing_startup_hook_is_reported_to_the_asgi_server(tmp_path: Path) -> None:
    app = App()

    @app.on_startup
    def fail() -> None:
        raise RuntimeError("database unreachable")

    with pytest.raises(RuntimeError, match="database unreachable"), app.test(interface="asgi"):
        pass


def test_without_threads_sync_handlers_run_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pyodide (Cloudflare Python Workers) cannot start threads, so `def` handlers must not try."""
    import threading

    from honoka import app as app_module

    monkeypatch.setattr(app_module, "NO_THREADS", True)
    app = App()
    seen: dict[str, int] = {}

    @app.get("/loop")
    async def on_loop(c: Context) -> Response:
        seen["loop"] = threading.get_ident()
        return c.text("")

    @app.get("/sync")
    def sync(c: Context) -> Response:
        seen["sync"] = threading.get_ident()
        return c.text("")

    with app.test(interface="asgi") as client:
        client.get("/loop")
        client.get("/sync")
    assert seen["sync"] == seen["loop"]
