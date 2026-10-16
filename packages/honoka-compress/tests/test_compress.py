"""compress."""

import gzip
import re
import zlib
from collections.abc import AsyncIterator

from honoka_compress import compress
from honoka_compress.compress import Encoding

from honoka import App, Context, Middleware, Response, Stream

BIG = "hello compress " * 200


def make_app(mw: Middleware | None = None) -> App:
    app = App()
    app.use("*", compress() if mw is None else mw)

    @app.get("/big")
    def big(c: Context) -> Response:
        return c.text(BIG, headers={"content-length": str(len(BIG)), "etag": '"v1"'})

    @app.get("/small")
    def small(c: Context) -> Response:
        return c.text("tiny")

    @app.get("/image")
    def image(c: Context) -> Response:
        return c.body(BIG, content_type="image/png")

    @app.get("/encoded")
    def encoded(c: Context) -> Response:
        return c.text(BIG, headers={"content-encoding": "br"})

    @app.get("/notransform")
    def notransform(c: Context) -> Response:
        return c.text(BIG, headers={"cache-control": "no-transform"})

    @app.get("/stream")
    def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str]:
            yield BIG

        return Stream(chunks(), headers=[("content-type", "text/plain")])

    return app


def get(app: App, path: str, accept: str | None) -> tuple[int, dict[str, str], bytes]:
    with app.test() as client:
        resp = client.get(path, headers=None if accept is None else {"accept-encoding": accept})
    return resp.status, resp.headers, resp.body


def test_gzip_is_chosen_and_round_trips() -> None:
    _, headers, body = get(make_app(), "/big", "gzip, deflate")
    assert headers["content-encoding"] == "gzip"
    assert headers["vary"] == "Accept-Encoding"
    assert "content-length" not in headers
    assert gzip.decompress(body).decode() == BIG


def test_deflate_when_gzip_is_not_accepted() -> None:
    _, headers, body = get(make_app(), "/big", "deflate")
    assert headers["content-encoding"] == "deflate"
    assert zlib.decompress(body).decode() == BIG


def test_q_values_pick_the_preferred_encoding_and_zero_refuses() -> None:
    app = make_app()
    assert get(app, "/big", "gzip;q=0.5, deflate;q=0.9")[1]["content-encoding"] == "deflate"
    assert get(app, "/big", "gzip;q=0, deflate")[1]["content-encoding"] == "deflate"
    assert get(app, "/big", "gzip;q=0")[1].get("content-encoding") is None
    assert get(app, "/big", "*")[1]["content-encoding"] == "gzip"


def test_the_encoding_option_pins_one_scheme() -> None:
    pinned: Encoding = "deflate"
    app = make_app(compress(encoding=pinned))
    assert get(app, "/big", "gzip, deflate")[1]["content-encoding"] == "deflate"
    assert get(app, "/big", "gzip")[1].get("content-encoding") is None


def test_a_strong_etag_becomes_weak() -> None:
    assert get(make_app(), "/big", "gzip")[1]["etag"] == 'W/"v1"'


def test_nothing_is_compressed_without_accept_encoding_or_for_unknown_ones() -> None:
    app = make_app()
    for accept in (None, "br", "identity"):
        _, headers, body = get(app, "/big", accept)
        assert "content-encoding" not in headers
        assert body.decode() == BIG


def test_bodies_under_the_threshold_are_left_alone_and_threshold_is_tunable() -> None:
    assert "content-encoding" not in get(make_app(), "/small", "gzip")[1]
    low = make_app(compress(threshold=1))
    assert get(low, "/small", "gzip")[1]["content-encoding"] == "gzip"


def test_skipped_for_incompressible_types_existing_encoding_and_no_transform() -> None:
    app = make_app()
    assert "content-encoding" not in get(app, "/image", "gzip")[1]
    assert get(app, "/encoded", "gzip")[1]["content-encoding"] == "br"
    assert "content-encoding" not in get(app, "/notransform", "gzip")[1]


def test_streams_are_passed_through_unbuffered() -> None:
    _, headers, body = get(make_app(), "/stream", "gzip")
    assert "content-encoding" not in headers
    assert body.decode() == BIG


def test_a_custom_content_type_filter() -> None:
    app = make_app(compress(content_type_filter=re.compile(r"^image/")))
    assert get(app, "/image", "gzip")[1]["content-encoding"] == "gzip"
    assert "content-encoding" not in get(app, "/big", "gzip")[1]
    by_function = make_app(compress(content_type_filter=lambda t: t.startswith("text/")))
    assert get(by_function, "/big", "gzip")[1]["content-encoding"] == "gzip"


def test_a_content_encoding_set_with_c_header_is_respected() -> None:
    app = App()
    app.use("*", compress())
    payload = gzip.compress(b"x" * 3000)

    @app.get("/already")
    def already(c: Context) -> Response:
        c.header("content-encoding", "gzip")
        return c.body(payload, headers={"content-type": "text/plain"})

    with app.test() as client:
        resp = client.get("/already", headers={"accept-encoding": "gzip"})
    assert resp.headers["content-encoding"] == "gzip"
    assert gzip.decompress(resp.body) == b"x" * 3000  # compressed once, not twice


def test_a_partial_response_is_left_alone() -> None:
    app = App()
    app.use("*", compress())

    @app.get("/range")
    def part(c: Context) -> Response:
        return c.body(
            b"x" * 2000,
            206,
            {"content-type": "text/plain", "content-range": "bytes 0-1999/9000"},
        )

    with app.test() as client:
        resp = client.get("/range", headers={"accept-encoding": "gzip"})
    assert (resp.status, resp.body) == (206, b"x" * 2000)
    assert "content-encoding" not in resp.headers
