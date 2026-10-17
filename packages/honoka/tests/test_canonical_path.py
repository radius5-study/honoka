"""One canonical request path: the router, the middleware gate and `c.req.path` agree on it."""

import pytest

from honoka import App, Context, Next, Response, WebSocket
from honoka.asgi import AsgiScope, Receive, Send


def guarded_app() -> App:
    app = App()
    seen: list[str] = []

    async def auth(c: Context, call_next: Next) -> Response:
        seen.append(c.req.path)
        if c.req.header("authorization") is None:
            return c.text("no", 401)
        return await call_next()

    app.use("/admin/*", auth)
    app.get("/admin/report", lambda c: c.text("secret report"))
    app.get("/open", lambda c: c.text(c.req.path))
    return app


@pytest.mark.parametrize(
    "path",
    [
        "//admin/report",
        "/./admin/report",
        "/a/../admin/report",
        "/admin//report",
        "/admin/./report",
    ],
)
def test_a_non_canonical_spelling_cannot_slip_past_a_path_scoped_middleware(path: str) -> None:
    with guarded_app().test() as client:
        assert client.get(path).status == 401
        assert client.get(path, headers={"authorization": "x"}).text == "secret report"


def test_the_handler_and_the_middleware_see_the_canonical_path() -> None:
    with guarded_app().test() as client:
        assert client.get("//./open").text == "/open"
        assert client.get("/x/y/../../open").text == "/open"


def test_dots_cannot_climb_above_the_root() -> None:
    with guarded_app().test() as client:
        assert client.get("/../../open").text == "/open"
        assert client.get("/admin/../../admin/report").status == 401


def test_a_single_trailing_slash_is_kept_so_strict_routing_still_tells_them_apart() -> None:
    app = App()
    app.get("/users", lambda c: c.text("no slash"))
    app.get("/users/", lambda c: c.text("slash"))
    with app.test() as client:
        assert client.get("/users").text == "no slash"
        assert client.get("/users/").text == "slash"
        assert client.get("/users//").text == "slash"
        assert client.get("//users").text == "no slash"


def test_a_mounted_app_is_guarded_by_the_canonical_path() -> None:
    app = App()
    seen: list[str] = []

    async def auth(c: Context, call_next: Next) -> Response:
        seen.append(c.req.path)
        return c.text("no", 401)

    async def mounted(scope: AsgiScope, receive: Receive, send: Send) -> None:
        raise AssertionError("the mounted app must not be reached")

    app.use("/inner/*", auth)
    app.mount("/inner", mounted)
    with app.test() as client:
        assert client.get("//inner/x").status == 401
        assert client.get("/a/../inner/x").status == 401
    assert seen == ["/inner/x", "/inner/x"]


def test_a_websocket_is_guarded_by_the_canonical_path() -> None:
    app = App()

    async def deny(c: Context, call_next: Next) -> Response:
        return c.text("no", 401)

    async def room(ws: WebSocket) -> None:
        await ws.accept()

    app.use("/private/*", deny)
    app.ws("/private/room", room)
    with app.test() as client:
        for spelling in ("/private/room", "//private/room", "/x/../private/./room"):
            with client.websocket(spelling) as ws:
                assert (ws.accepted, ws.close_code) == (False, 1008), spelling
