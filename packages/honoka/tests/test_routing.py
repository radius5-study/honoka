"""Path patterns, route priority and the HTTP method decorators, via the public test client."""

import pytest

from honoka import App, Context, Response, router


@pytest.fixture(autouse=True, params=["regex", "trie"])
def strategy(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Every routing behavior must hold whichever way the router matches (see REGEX_ROUTE_LIMIT)."""
    limit = router.REGEX_ROUTE_LIMIT if request.param == "regex" else 0
    monkeypatch.setattr(router, "REGEX_ROUTE_LIMIT", limit)
    return str(request.param)


def echo(app: App, method: str, pattern: str, *names: str) -> None:
    """Register `pattern` so that it answers with the path parameters it captured."""

    def handler(c: Context) -> Response:
        return c.text(",".join(f"{n}={c.req.param(n)}" for n in names) or c.req.path)

    app.on(method, pattern)(handler)


def test_a_typed_parameter_only_matches_what_its_type_allows() -> None:
    app = App()
    echo(app, "GET", "/users/{id:int}", "id")
    with app.test() as client:
        assert client.get("/users/42").text == "id=42"
        assert client.get("/users/abc").status == 404
        assert client.get("/users/4.2").status == 404


def test_typed_and_generic_parameters_can_share_a_position() -> None:
    app = App()
    echo(app, "GET", "/u/{id:int}", "id")
    echo(app, "GET", "/u/{name}", "name")
    with app.test() as client:
        assert client.get("/u/7").text == "id=7"
        assert client.get("/u/bob").text == "name=bob"


@pytest.mark.parametrize(
    ("converter", "good", "bad"),
    [
        ("float", "3.14", "3.14.1"),
        ("uuid", "123e4567-e89b-12d3-a456-426614174000", "123e4567"),
        ("slug", "my-post_1", "my post"),
    ],
)
def test_each_builtin_converter_accepts_its_shape_and_rejects_others(
    converter: str, good: str, bad: str
) -> None:
    app = App()
    echo(app, "GET", f"/x/{{v:{converter}}}", "v")
    with app.test() as client:
        assert client.get(f"/x/{good}").text == f"v={good}"
        assert client.get(f"/x/{bad}").status == 404


def test_a_path_parameter_captures_the_rest_including_slashes() -> None:
    app = App()
    echo(app, "GET", "/static/{rest:path}", "rest")
    with app.test() as client:
        assert client.get("/static/css/app.css").text == "rest=css/app.css"
        assert client.get("/static/").status == 404


def test_a_trailing_wildcard_matches_the_prefix_itself_and_everything_below_it() -> None:
    app = App()
    echo(app, "GET", "/files/*")
    with app.test() as client:
        assert client.get("/files").text == "/files"
        assert client.get("/files/a/b.txt").text == "/files/a/b.txt"
        assert client.get("/other").status == 404


def test_a_root_wildcard_matches_every_path() -> None:
    app = App()
    echo(app, "GET", "/*")
    with app.test() as client:
        assert client.get("/").status == 200
        assert client.get("/a/b/c").text == "/a/b/c"


def test_more_specific_routes_win_static_then_typed_then_generic_then_wildcard() -> None:
    app = App()
    echo(app, "GET", "/a/*")
    echo(app, "GET", "/a/{name}", "name")
    echo(app, "GET", "/a/{id:int}", "id")
    app.get("/a/me")(lambda c: c.text("static"))
    with app.test() as client:
        assert client.get("/a/me").text == "static"
        assert client.get("/a/12").text == "id=12"
        assert client.get("/a/bob").text == "name=bob"
        assert client.get("/a/x/y").text == "/a/x/y"


def test_literal_characters_in_a_pattern_are_not_regular_expression_syntax() -> None:
    app = App()
    echo(app, "GET", "/a.b/{id}", "id")
    with app.test() as client:
        assert client.get("/a.b/1").text == "id=1"
        assert client.get("/aXb/1").status == 404


def test_hundreds_of_routes_each_resolve_to_their_own_handler_and_parameters() -> None:
    app = App()
    for i in range(300):
        echo(app, "GET", f"/r{i}/{{a}}/x/{{b:int}}", "a", "b")
    with app.test() as client:
        for i in (0, 1, 149, 150, 299):
            assert client.get(f"/r{i}/q/x/{i}").text == f"a=q,b={i}"
        assert client.get("/r5/q/x/notint").status == 404


def test_methods_are_tracked_per_route_for_405_and_options() -> None:
    app = App()
    echo(app, "GET", "/items/{id:int}", "id")
    echo(app, "DELETE", "/items/{id:int}", "id")
    with app.test() as client:
        denied = client.request("POST", "/items/3")
        listed = client.request("OPTIONS", "/items/3")
    assert denied.status == 405
    assert denied.headers["allow"] == "DELETE, GET, HEAD"
    assert listed.headers["allow"] == "DELETE, GET, HEAD"


def test_by_default_a_trailing_slash_is_a_different_path() -> None:
    app = App()
    echo(app, "GET", "/hello")
    with app.test() as client:
        assert client.get("/hello").status == 200
        assert client.get("/hello/").status == 404


def test_a_non_strict_app_ignores_the_trailing_slash() -> None:
    app = App(strict=False)
    echo(app, "GET", "/hello")
    echo(app, "GET", "/users/{id:int}", "id")
    with app.test() as client:
        assert client.get("/hello/").status == 200
        assert client.get("/users/5/").text == "id=5"
        assert client.get("/hello").status == 200


def test_head_options_on_and_all_register_the_methods_they_name() -> None:
    app = App()

    @app.head("/h")
    def head(c: Context) -> Response:
        return c.text("", 204)

    @app.options("/o")
    def options(c: Context) -> Response:
        return c.text("custom", 200)

    @app.on(["GET", "POST"], "/m")
    def both(c: Context) -> Response:
        return c.text(c.req.method)

    @app.all("/any")
    def anything(c: Context) -> Response:
        return c.text(c.req.method)

    with app.test() as client:
        assert client.request("HEAD", "/h").status == 204
        assert client.request("OPTIONS", "/o").text == "custom"
        assert [client.request(m, "/m").text for m in ("GET", "POST")] == ["GET", "POST"]
        assert client.request("PUT", "/m").status == 405
        assert [client.request(m, "/any").text for m in ("GET", "DELETE", "PATCH")] == [
            "GET",
            "DELETE",
            "PATCH",
        ]


@pytest.mark.parametrize(
    "pattern",
    ["no-leading-slash", "/a/{x", "/a/{x:nope}", "/a/*/b", "/a/{p:path}/b", "/a/b*c", "/a/{}"],
)
def test_a_malformed_pattern_is_rejected_when_the_route_is_declared(pattern: str) -> None:
    app = App()
    with pytest.raises(ValueError, match="pattern"):
        app.get(pattern)(lambda c: c.text(""))


def test_sibling_literals_that_share_prefixes_each_keep_their_own_parameters() -> None:
    """Prefix-sharing siblings are folded together in the regex; none may steal another's groups."""
    app = App()
    for literal in ("ac", "abc", "b", "a", "ab", "abd", "x1", "x12", "x2"):
        echo(app, "GET", f"/{literal}/{{p}}/{{q:int}}", "p", "q")
    with app.test() as client:
        for literal in ("a", "ab", "abc", "abd", "ac", "b", "x1", "x12", "x2"):
            assert client.get(f"/{literal}/one/7").text == "p=one,q=7", literal
        assert client.get("/abcd/one/7").status == 404
        assert client.get("/ab/one/seven").status == 404
