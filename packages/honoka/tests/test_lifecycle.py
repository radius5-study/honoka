"""Route groups, startup/shutdown hooks and app-wide values."""

import pytest

from honoka import App, Context, Key, Next, Response

DB: Key[str] = Key("db")


def test_a_sub_app_is_mounted_under_a_prefix_with_its_own_middleware_scoped_to_it() -> None:
    users = App()

    async def mark(c: Context, call_next: Next) -> Response:
        response = await call_next()
        response.headers.append(("x-group", "users"))
        return response

    users.use("*", mark)

    @users.get("/")
    def index(c: Context) -> Response:
        return c.text("list")

    @users.get("/{user_id}")
    def show(c: Context) -> Response:
        return c.text(f"user {c.req.param('user_id')}")

    app = App()
    app.route("/users", users)

    @app.get("/health")
    def health(c: Context) -> Response:
        return c.text("ok")

    with app.test() as client:
        listing = client.get("/users")
        one = client.get("/users/7")
        outside = client.get("/health")

    assert (listing.text, one.text) == ("list", "user 7")
    assert listing.headers["x-group"] == "users"
    assert "x-group" not in outside.headers


def test_startup_hooks_run_before_requests_and_shutdown_hooks_after_in_reverse() -> None:
    app = App()
    events: list[str] = []

    @app.on_startup
    def first_up() -> None:
        events.append("up 1")

    @app.on_startup
    async def second_up() -> None:
        events.append("up 2")

    @app.on_shutdown
    def first_down() -> None:
        events.append("down 1")

    @app.on_shutdown
    async def second_down() -> None:
        events.append("down 2")

    @app.get("/")
    def handler(c: Context) -> Response:
        events.append("request")
        return c.text("")

    with app.test() as client:
        client.get("/")
        client.get("/")

    assert events == ["up 1", "up 2", "request", "request", "down 2", "down 1"]


def test_a_value_published_at_startup_is_readable_by_every_request() -> None:
    app = App()

    @app.on_startup
    def connect() -> None:
        app.provide(DB, "pool")

    @app.get("/")
    def handler(c: Context) -> Response:
        return c.text(c.get(DB))

    with app.test() as client:
        assert client.get("/").text == "pool"


def test_a_failing_startup_hook_stops_the_app_from_serving() -> None:
    app = App()

    @app.on_startup
    def connect() -> None:
        raise RuntimeError("database unreachable")

    with pytest.raises(RuntimeError, match="database unreachable"), app.test():
        pass
