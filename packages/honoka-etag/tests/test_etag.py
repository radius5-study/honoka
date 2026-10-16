"""etag."""

import hashlib
from collections.abc import AsyncIterator

from honoka_etag import etag

from honoka import App, Context, Middleware, Response, Stream


def make_app(mw: Middleware | None = None) -> App:
    app = App()
    app.use("*", etag() if mw is None else mw)

    @app.get("/")
    def index(c: Context) -> Response:
        return c.text(
            "hello",
            headers={"cache-control": "max-age=60", "x-extra": "1"},
        )

    @app.get("/preset")
    def preset(c: Context) -> Response:
        return c.text("hello", headers={"etag": '"mine"'})

    @app.get("/stream")
    def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str]:
            yield "x"

        return Stream(chunks())

    @app.get("/empty")
    def empty(c: Context) -> Response:
        return c.text("")

    @app.post("/")
    def post(c: Context) -> Response:
        return c.text("hello")

    return app


DIGEST = hashlib.sha1(b"hello", usedforsecurity=False).hexdigest()


def test_a_strong_sha1_etag_is_added() -> None:
    with make_app().test() as client:
        resp = client.get("/")
    assert resp.status == 200
    assert resp.headers["etag"] == f'"{DIGEST}"'


def test_the_weak_option_prefixes_w() -> None:
    with make_app(etag(weak=True)).test() as client:
        resp = client.get("/")
    assert resp.headers["etag"] == f'W/"{DIGEST}"'


def test_a_matching_if_none_match_gets_304_with_only_retained_headers() -> None:
    with make_app().test() as client:
        resp = client.get("/", headers={"if-none-match": f'"{DIGEST}"'})
    assert resp.status == 304
    assert resp.body == b""
    assert resp.headers["etag"] == f'"{DIGEST}"'
    assert resp.headers["cache-control"] == "max-age=60"
    assert "x-extra" not in resp.headers


def test_a_weak_or_listed_or_star_condition_matches() -> None:
    with make_app().test() as client:
        weak = client.get("/", headers={"if-none-match": f'W/"{DIGEST}"'})
        listed = client.get("/", headers={"if-none-match": f'"other", "{DIGEST}"'})
        star = client.get("/", headers={"if-none-match": "*"})
    assert (weak.status, listed.status, star.status) == (304, 304, 304)


def test_a_different_if_none_match_gets_the_full_response() -> None:
    with make_app().test() as client:
        resp = client.get("/", headers={"if-none-match": '"stale"'})
    assert (resp.status, resp.text) == (200, "hello")


def test_an_etag_already_set_is_kept() -> None:
    with make_app().test() as client:
        resp = client.get("/preset")
        cond = client.get("/preset", headers={"if-none-match": '"mine"'})
    assert resp.headers["etag"] == '"mine"'
    assert cond.status == 304


def test_other_methods_streams_and_empty_bodies_are_left_alone() -> None:
    with make_app().test() as client:
        post = client.post("/", headers={"if-none-match": "*"})
        stream = client.get("/stream", headers={"if-none-match": "*"})
        empty = client.get("/empty")
    assert (post.status, "etag" in post.headers) == (200, False)
    assert (stream.status, "etag" in stream.headers) == (200, False)
    assert "etag" not in empty.headers


def test_retain_headers_replaces_the_kept_list() -> None:
    app = App()
    app.use("*", etag(retained_headers=["etag", "x-extra"]))

    @app.get("/")
    def index(c: Context) -> Response:
        return c.text("hello", headers={"x-extra": "1", "cache-control": "no-store"})

    with app.test() as client:
        resp = client.get("/", headers={"if-none-match": "*"})
    assert resp.status == 304
    assert resp.headers["x-extra"] == "1"
    assert "cache-control" not in resp.headers


def test_an_etag_set_with_c_header_is_the_one_a_conditional_request_is_compared_with() -> None:
    app = App()
    app.use("*", etag())

    @app.get("/")
    def index(c: Context) -> Response:
        c.header("etag", '"v1"')
        return c.text("hello")

    with app.test() as client:
        full = client.get("/")
        cached = client.get("/", headers={"if-none-match": '"v1"'})
    assert (full.status, full.headers["etag"]) == (200, '"v1"')
    assert cached.status == 304
