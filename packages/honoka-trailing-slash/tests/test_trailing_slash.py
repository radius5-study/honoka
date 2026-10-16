"""trim_trailing_slash."""

from honoka_trailing_slash import trim_trailing_slash

from honoka import App, Context, Response


def trailing_app() -> App:
    app = App()
    app.use("*", trim_trailing_slash())

    @app.get("/users")
    def users(c: Context) -> Response:
        return c.text("users")

    @app.get("/docs/")
    def docs(c: Context) -> Response:
        return c.text("docs")

    @app.post("/users")
    def create(c: Context) -> Response:
        return c.text("created")

    return app


def test_a_trailing_slash_that_would_404_redirects_to_the_trimmed_path_keeping_the_query() -> None:
    with trailing_app().test() as client:
        resp = client.get("/users/?page=2")
    assert (resp.status, resp.headers["location"]) == (301, "/users?page=2")


def test_a_route_declared_with_a_trailing_slash_keeps_working() -> None:
    with trailing_app().test() as client:
        assert client.get("/docs/").text == "docs"


def test_only_get_is_redirected_and_unknown_paths_stay_404() -> None:
    with trailing_app().test() as client:
        assert client.post("/users/").status == 404
        assert client.get("/nowhere").status == 404
        assert client.get("/").status == 404


def test_leading_slashes_are_collapsed_so_the_redirect_stays_on_this_site() -> None:
    with trailing_app().test() as client:
        resp = client.get("//evil.test/")
    assert resp.headers["location"] == "/evil.test"
