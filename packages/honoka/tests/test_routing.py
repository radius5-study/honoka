"""Path patterns, route priority and the HTTP method registrations, via the public test client."""

import re

import pytest

from honoka import App, Context, Response, router
from honoka import app as app_module


@pytest.fixture(
    autouse=True,
    params=["RegExpRouter", "TrieRouter", "SmartRouter", "SmartRouter without RegExpRouter"],
)
def strategy(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Every routing behavior must hold on each router, and on the SmartRouter in both outcomes:
    keeping the RegExpRouter, and falling back to the TrieRouter when it cannot be built."""
    name = str(request.param)
    if name == "RegExpRouter":
        monkeypatch.setattr(app_module, "DEFAULT_ROUTER", router.RegExpRouter)
    elif name == "TrieRouter":
        monkeypatch.setattr(app_module, "DEFAULT_ROUTER", router.TrieRouter)
    elif name == "SmartRouter without RegExpRouter":

        def refuse(root: object) -> object:
            raise re.error("simulated: the pattern is too large")

        monkeypatch.setattr(router, "_regex_matcher", refuse)
    return name


def echo(app: App, method: str, pattern: str, *names: str) -> None:
    """Register `pattern` so that it answers with the path parameters it captured."""

    def handler(c: Context) -> Response:
        return c.text(",".join(f"{n}={c.req.param(n)}" for n in names) or c.req.path)

    app.on(method, pattern, handler)


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
    app.get("/a/me", lambda c: c.text("static"))
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

    def head(c: Context) -> Response:
        return c.text("", 204)

    app.head("/h", head)

    def options(c: Context) -> Response:
        return c.text("custom", 200)

    app.options("/o", options)

    def both(c: Context) -> Response:
        return c.text(c.req.method)

    app.on(["GET", "POST"], "/m", both)

    def anything(c: Context) -> Response:
        return c.text(c.req.method)

    app.all("/any", anything)

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
    [
        "no-leading-slash",
        "/a/{x",
        "/a/{x:nope}",
        "/a/{p:path}/b",
        "/a/{}",
        "/a/{x:re:(a)}",
        "/a/{x:re:[}",
        "/a/{x:re:a*}",
        "/a/{x}?/b",
    ],
)
def test_a_malformed_pattern_is_rejected_when_the_route_is_declared(pattern: str) -> None:
    app = App()
    with pytest.raises(ValueError, match="pattern"):
        app.get(pattern, lambda c: c.text(""))


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


def test_a_lone_star_in_the_middle_is_one_non_empty_segment() -> None:
    """Hono v5: `/wild/*/card` matches `/wild/x/card` but not `/wild//card` or two segments."""
    app = App()
    echo(app, "GET", "/wild/*/card")
    with app.test() as client:
        assert client.get("/wild/x/card").status == 200
        assert client.get("/wild//card").status == 404
        assert client.get("/wild/x/y/card").status == 404


def test_a_star_ending_a_segment_matches_any_suffix_including_nothing() -> None:
    app = App()
    echo(app, "GET", "/img*/list")
    with app.test() as client:
        assert [client.get(f"/{p}/list").status for p in ("img", "img9", "imgs-big")] == [200] * 3
        assert client.get("/im/list").status == 404
        assert client.get("/img/x/list").status == 404


def test_a_star_inside_a_segment_is_an_ordinary_character() -> None:
    app = App()
    echo(app, "GET", "/x*y")
    with app.test() as client:
        assert client.get("/x*y").status == 200
        assert client.get("/xay").status == 404


def test_an_optional_last_segment_matches_with_and_without_it() -> None:
    app = App()
    echo(app, "GET", "/api/animal/{type}?")

    def probe(c: Context) -> Response:
        return c.text("")

    app.get("/probe", probe)

    with app.test() as client:
        assert client.get("/api/animal").status == 200
        assert client.get("/api/animal/cat").status == 200
        assert client.get("/api/animal/cat/extra").status == 404


def test_an_optional_parameter_is_absent_from_params_when_left_out() -> None:
    app = App()

    def files(c: Context) -> Response:
        return c.text(c.req.params.get("name", "none"))

    app.get("/files/{name:slug}?", files)

    with app.test() as client:
        assert client.get("/files").text == "none"
        assert client.get("/files/a-b").text == "a-b"


def test_a_regex_parameter_only_matches_what_the_expression_allows() -> None:
    app = App()
    echo(app, "GET", "/post/{date:re:[0-9]+}/{title:re:[a-z]+}", "date", "title")
    with app.test() as client:
        assert client.get("/post/20260102/hello").text == "date=20260102,title=hello"
        assert client.get("/post/2026a/hello").status == 404
        assert client.get("/post/1/HELLO").status == 404


def test_a_regex_parameter_may_span_slashes() -> None:
    app = App()
    echo(app, "GET", "/posts/{filename:re:.+\\.png}", "filename")
    with app.test() as client:
        assert client.get("/posts/a/b/c.png").text == "filename=a/b/c.png"
        assert client.get("/posts/a.jpg").status == 404


def test_a_regex_parameter_ranks_between_fixed_converters_and_a_plain_one() -> None:
    app = App()
    echo(app, "GET", "/c/{n:int}", "n")
    echo(app, "GET", "/c/{code:re:[A-Z]{3}}", "code")
    echo(app, "GET", "/c/{any}", "any")
    with app.test() as client:
        assert client.get("/c/12").text == "n=12"
        assert client.get("/c/ABC").text == "code=ABC"
        assert client.get("/c/abc").text == "any=abc"


def test_newlines_in_a_path_are_matched_the_same_way_by_every_router() -> None:
    app = App()
    echo(app, "GET", "/f/{p:path}", "p")
    echo(app, "GET", "/a/*")
    with app.test() as client:
        assert client.get("/f/a\nb").text == "p=a\nb"
        assert client.get("/a/b\nc").status == 200


@pytest.mark.parametrize(
    "pattern",
    ["/u/{id:re:^[0-9]+$}", "/u/{id:re:[0-9]+$}", "/u/{id:re:\\Aabc}", "/u/{id:re:(?<=x)y}"],
)
def test_a_regex_that_looks_outside_its_own_segment_is_rejected(pattern: str) -> None:
    app = App()
    with pytest.raises(ValueError, match="pattern"):
        app.get(pattern, lambda c: c.text(""))


def test_a_caret_inside_a_character_class_is_not_an_anchor() -> None:
    app = App()
    echo(app, "GET", "/u/{id:re:[^/x]+}", "id")
    with app.test() as client:
        assert client.get("/u/12").text == "id=12"
        assert client.get("/u/x").status == 404
