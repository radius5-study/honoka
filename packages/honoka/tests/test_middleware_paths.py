"""A middleware path means what a route path means: one set of rules, one matcher."""

import pytest

from honoka import App, Context, Middleware, Next, Response


def deny() -> Middleware:
    async def middleware(c: Context, call_next: Next) -> Response:
        if c.req.header("authorization") is None:
            return c.text("denied", 401)
        return await call_next()

    return middleware


def status(app: App, path: str, *, signed_in: bool = False) -> int:
    headers = {"authorization": "x"} if signed_in else None
    with app.test() as client:
        return client.get(path, headers=headers).status


@pytest.mark.parametrize(
    ("pattern", "covered", "uncovered"),
    [
        ("/users/{id}/*", ["/users/7", "/users/7/settings"], ["/users", "/accounts/7/x"]),
        ("/users/{id:int}/*", ["/users/7/settings"], ["/users/abc/settings"]),
        ("/a/*/c", ["/a/b/c"], ["/a/c", "/a/b/c/d"]),
        ("/admin*", ["/admin", "/administrator"], ["/other", "/admin/x"]),
        ("/docs/{rest:path}", ["/docs/a/b/c"], ["/other/a"]),
        ("/img/{name:re:[a-z]+\\.png}", ["/img/cat.png"], ["/img/cat.jpg"]),
        ("/api/{v}?", ["/api", "/api/v1"], ["/other"]),
    ],
)
def test_a_middleware_pattern_covers_exactly_what_a_route_of_that_pattern_matches(
    pattern: str, covered: list[str], uncovered: list[str]
) -> None:
    app = App()
    app.use(pattern, deny())
    app.get("/{anything:path}", lambda c: c.text("ok"))
    app.get("/", lambda c: c.text("ok"))
    for path in covered:
        assert status(app, path) == 401, path
        assert status(app, path, signed_in=True) == 200, path
    for path in uncovered:
        assert status(app, path) == 200, path


def test_a_regex_parameter_that_spans_segments_does_not_drop_the_middleware() -> None:
    app = App()
    app.use("/files/secret/x/*", deny())
    app.get("/files/{p:re:.+}/download", lambda c: c.text("file"))
    assert status(app, "/files/secret/x/download") == 401
    assert status(app, "/files/public/download") == 200


def test_without_strict_a_trailing_slash_is_covered_like_the_route_it_reaches() -> None:
    app = App(strict=False)
    app.use("/admin", deny())
    app.get("/admin/", lambda c: c.text("panel"))
    for path in ("/admin", "/admin/"):
        assert status(app, path) == 401, path


@pytest.mark.parametrize("pattern", ["/users/{id", "admin", "/a/{x:nope}", "/a/{p:path}/b"])
def test_a_pattern_the_router_would_refuse_is_refused_when_the_middleware_is_registered(
    pattern: str,
) -> None:
    with pytest.raises(ValueError, match="pattern"):
        App().use(pattern, deny())


def test_an_error_fallback_scoped_by_a_route_style_path_applies() -> None:
    app = App()

    async def boom(c: Context) -> Response:
        raise RuntimeError("x")

    app.get("/users/{id}", boom)
    app.get("/other", boom)
    app.on_error("/users/{id}", lambda c: c.text("user failure", 500))
    with app.test() as client:
        assert client.get("/users/7").text == "user failure"
        assert client.get("/other").text != "user failure"
