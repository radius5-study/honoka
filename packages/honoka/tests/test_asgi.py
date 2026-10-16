"""The ASGI entry as a server sees it: responses, lifespan, lazy start and host quirks."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

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


def test_responses_carry_status_headers_cookies_and_streamed_bodies(tmp_path: Path) -> None:
    with build(tmp_path)[0].test() as client:
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
    """HTTP/2 clients may send `cookie` as separate fields; they must read as one header."""
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


def test_a_redirect_to_a_non_ascii_path_is_sent_as_utf_8(tmp_path: Path) -> None:
    with build(tmp_path)[0].test() as client:
        assert client.get("/jump").headers["location"] == "/日本語"


@pytest.mark.parametrize(
    ("method", "path", "status", "body"),
    [
        ("GET", "/missing", 404, b'{"error":"not_found"}'),
        ("DELETE", "/text", 405, b'{"error":"method_not_allowed"}'),
        ("OPTIONS", "/text", 204, b""),
        ("HEAD", "/text", 200, b""),
        ("HEAD", "/stream", 200, b""),
        ("GET", "/users/abc", 404, b'{"error":"not_found"}'),
        ("GET", "/files/a/b.txt", 200, b"/files/a/b.txt"),
        ("GET", "/file", 200, b"from disk"),
    ],
)
def test_status_and_body_for_the_common_outcomes(
    method: str, path: str, status: int, body: bytes, tmp_path: Path
) -> None:
    with build(tmp_path)[0].test() as client:
        response = client.request(method, path)
    assert (response.status, response.body) == (status, body)


def test_request_parts_reach_the_handler(tmp_path: Path) -> None:
    with build(tmp_path)[0].test() as client:
        got = client.get(
            "/users/7?q=hi%20there&tag=a&tag=b", headers={"user-agent": "t", "cookie": "sid=s1"}
        ).json()
        echoed = client.post("/echo", json={"a": [1, 2, {"b": None}]})
    assert got == {"id": 7, "q": "hi there", "tags": ["a", "b"], "agent": "t", "cookie": "s1"}
    assert (echoed.status, echoed.json()) == (201, {"body": {"a": [1, 2, {"b": None}]}})


class _Lifespan:
    """Drives the app's ASGI lifespan protocol the way an ASGI server does."""

    def __init__(self, app: App) -> None:
        self._app = app
        self._inbox: asyncio.Queue[AsgiMessage] = asyncio.Queue()
        self._outbox: asyncio.Queue[AsgiMessage] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        scope: AsgiMessage = {"type": "lifespan", "asgi": {"version": "3.0"}}
        self._task = asyncio.create_task(self._app(scope, self._inbox.get, self._outbox.put))
        await self._inbox.put({"type": "lifespan.startup"})
        reply = await self._outbox.get()
        if reply["type"] != "lifespan.startup.complete":
            raise RuntimeError(str(reply.get("message", "startup failed")))

    async def shutdown(self) -> None:
        await self._inbox.put({"type": "lifespan.shutdown"})
        reply = await self._outbox.get()
        if self._task is not None:
            await self._task
        if reply["type"] != "lifespan.shutdown.complete":
            raise RuntimeError(str(reply.get("message", "shutdown failed")))


def test_lifespan_runs_startup_and_shutdown_hooks_through_the_asgi_protocol(
    tmp_path: Path,
) -> None:
    app, log = build(tmp_path)

    async def serve() -> None:
        lifespan = _Lifespan(app)
        await lifespan.startup()
        assert log == ["up"]
        await lifespan.shutdown()

    asyncio.run(serve())
    assert log == ["up", "down"]


def test_a_failing_startup_hook_is_reported_to_the_asgi_server() -> None:
    app = App()

    @app.on_startup
    def fail() -> None:
        raise RuntimeError("database unreachable")

    with pytest.raises(RuntimeError, match="database unreachable"):
        asyncio.run(_Lifespan(app).startup())
