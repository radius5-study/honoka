"""Routing and handlers, observed through the public test client (the ASGI entry, driven in process)."""

import threading

import pytest

from honoka import App, Context, Json, Response


def test_sync_handler_responds_with_plain_text() -> None:
    app = App()

    @app.get("/health")
    def health(c: Context) -> Response:
        return c.text("ok")

    with app.test() as client:
        resp = client.get("/health")

    assert resp.status == 200
    assert resp.text == "ok"
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"


def test_async_handler_reads_a_typed_path_parameter_and_returns_json() -> None:
    app = App()

    @app.get("/users/{user_id}")
    async def get_user(c: Context) -> Json[dict[str, object]]:
        return c.json({"id": c.req.param("user_id", int), "name": "a"})

    with app.test() as client:
        resp = client.get("/users/42")

    assert resp.status == 200
    assert resp.json() == {"id": 42, "name": "a"}
    assert resp.headers["content-type"] == "application/json"


def test_def_handlers_leave_the_event_loop_thread_unless_inline() -> None:
    app = App()
    threads: dict[str, int] = {}

    @app.get("/loop")
    async def on_loop(c: Context) -> Response:
        threads["loop"] = threading.get_ident()
        return c.text("")

    @app.get("/threaded")
    def threaded(c: Context) -> Response:
        threads["threaded"] = threading.get_ident()
        return c.text("")

    @app.get("/inline", inline=True)
    def inline(c: Context) -> Response:
        threads["inline"] = threading.get_ident()
        return c.text("")

    with app.test() as client:
        for path in ("/loop", "/threaded", "/inline"):
            client.get(path)

    assert threads["threaded"] != threads["loop"]
    assert threads["inline"] == threads["loop"]


def test_html_response_has_an_html_content_type() -> None:
    app = App()

    @app.get("/")
    def index(c: Context) -> Response:
        return c.html("<p>hi</p>")

    with app.test() as client:
        resp = client.get("/")

    assert resp.text == "<p>hi</p>"
    assert resp.headers["content-type"] == "text/html; charset=utf-8"


def make_app() -> App:
    app = App()

    @app.get("/items")
    def list_items(c: Context) -> Response:
        return c.text("list")

    @app.post("/items")
    def create_item(c: Context) -> Response:
        return c.text("created", 201)

    @app.get("/users/me")
    def me(c: Context) -> Response:
        return c.text("me")

    @app.get("/users/{user_id}")
    def user(c: Context) -> Response:
        return c.text(f"user {c.req.param('user_id')}")

    return app


def test_unknown_path_is_a_structured_404() -> None:
    with make_app().test() as client:
        resp = client.get("/nope")
    assert resp.status == 404
    assert resp.json() == {"error": "not_found"}


def test_known_path_with_the_wrong_method_is_a_405_listing_allowed_methods() -> None:
    with make_app().test() as client:
        resp = client.request("DELETE", "/items")
    assert resp.status == 405
    assert resp.headers["allow"] == "GET, HEAD, POST"
    assert resp.json() == {"error": "method_not_allowed"}


def test_head_answers_like_get_without_a_body() -> None:
    with make_app().test() as client:
        resp = client.request("HEAD", "/items")
    assert resp.status == 200
    assert resp.body == b""
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"


def test_options_on_a_known_path_lists_allowed_methods() -> None:
    with make_app().test() as client:
        resp = client.request("OPTIONS", "/items")
    assert resp.status == 204
    assert resp.headers["allow"] == "GET, HEAD, POST"


def test_a_static_route_wins_over_a_parameter_route() -> None:
    with make_app().test() as client:
        assert client.get("/users/me").text == "me"
        assert client.get("/users/7").text == "user 7"


def test_matching_backtracks_from_a_dead_end_static_branch() -> None:
    app = App()

    @app.get("/u/me/{x}/q")
    def first(c: Context) -> Response:
        return c.text("first")

    @app.get("/u/{id}/y/z")
    def second(c: Context) -> Response:
        return c.text(f"second {c.req.param('id')}")

    with app.test() as client:
        assert client.get("/u/me/y/z").text == "second me"


def test_registering_the_same_method_and_path_twice_is_rejected() -> None:
    app = App()

    @app.get("/dup")
    def one(c: Context) -> Response:
        return c.text("1")

    with pytest.raises(ValueError, match="duplicate route: GET /dup"):

        @app.get("/dup")
        def two(c: Context) -> Response:
            return c.text("2")


def test_conflicting_parameter_names_fail_when_the_app_starts_not_on_a_request() -> None:
    app = App()

    @app.get("/a/{x}")
    def first(c: Context) -> Response:
        return c.text("1")

    @app.get("/a/{y}/b")
    def second(c: Context) -> Response:
        return c.text("2")

    with pytest.raises(ValueError, match="conflicting parameter names"), app.test():
        pass
