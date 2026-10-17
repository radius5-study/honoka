"""Response-side helpers: cookies and streaming, via the public test client."""

from collections.abc import AsyncIterator
from pathlib import Path

from honoka import App, Context, Response


def test_set_and_delete_cookie_emit_one_set_cookie_header_each() -> None:
    app = App()

    def handler(c: Context) -> Response:
        c.cookie("sid", "abc", max_age=60)
        c.cookie("theme", "dark", http_only=False, same_site="Strict", secure=True)
        c.delete_cookie("old")
        return c.text("ok")

    app.get("/", handler)

    with app.test() as client:
        resp = client.get("/")

    cookies = [v for k, v in resp.raw_headers if k == "set-cookie"]
    assert cookies == [
        "sid=abc; Path=/; Max-Age=60; HttpOnly; SameSite=Lax",
        "theme=dark; Path=/; Secure; SameSite=Strict",
        "old=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
    ]


def test_cookies_set_before_an_error_still_reach_the_client() -> None:
    app = App()

    def handler(c: Context) -> Response:
        c.cookie("sid", "abc")
        raise RuntimeError("boom")

    app.get("/", handler)

    with app.test() as client:
        resp = client.get("/")

    assert resp.status == 500
    assert ("set-cookie", "sid=abc; Path=/; HttpOnly; SameSite=Lax") in resp.raw_headers


def test_stream_sends_chunks_in_order_with_the_given_content_type() -> None:
    app = App()

    async def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str | bytes]:
            yield "one,"
            yield b"two,"
            yield "three"

        return c.stream(chunks(), content_type="text/csv")

    app.get("/stream", stream)

    with app.test() as client:
        resp = client.get("/stream")

    assert resp.status == 200
    assert resp.text == "one,two,three"
    assert resp.headers["content-type"] == "text/csv"


def test_a_cookie_value_that_could_inject_a_header_is_rejected() -> None:
    app = App()

    def handler(c: Context) -> Response:
        try:
            c.cookie("sid", "x\r\nset-cookie: evil=1")
        except ValueError:
            return c.text("rejected")
        return c.text("accepted")

    app.get("/", handler)

    with app.test() as client:
        resp = client.get("/")

    assert resp.text == "rejected"
    assert not [v for k, v in resp.raw_headers if k == "set-cookie"]


def test_cookie_attributes_that_could_inject_a_header_or_attribute_are_rejected() -> None:
    app = App()

    def handler(c: Context) -> Response:
        rejected = 0
        for kwargs in ({"path": "/; Domain=evil.test"}, {"domain": "a.test\r\nx: y"}):
            try:
                c.cookie("sid", "v", **kwargs)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            except ValueError:
                rejected += 1
        return c.text(str(rejected))

    app.get("/", handler)

    with app.test() as client:
        assert client.get("/").text == "2"


def test_head_on_a_streaming_route_sends_headers_but_no_chunks() -> None:
    app = App()

    async def stream(c: Context) -> Response:
        async def chunks() -> AsyncIterator[str | bytes]:
            yield "data"

        return c.stream(chunks(), content_type="text/plain")

    app.get("/stream", stream)

    with app.test() as client:
        resp = client.request("HEAD", "/stream")

    assert (resp.status, resp.body) == (200, b"")
    assert resp.headers["content-type"] == "text/plain"


def test_redirect_sets_the_location_and_rejects_header_injection() -> None:
    app = App()

    def go(c: Context) -> Response:
        return c.redirect("/there", 301)

    app.get("/go", go)

    def bad(c: Context) -> Response:
        try:
            return c.redirect("/x\r\nset-cookie: a=b")
        except ValueError:
            return c.text("rejected")

    app.get("/bad", bad)

    with app.test() as client:
        moved = client.get("/go")
        rejected = client.get("/bad")

    assert (moved.status, moved.headers["location"]) == (301, "/there")
    assert rejected.text == "rejected"


def file_app(root: Path) -> App:
    app = App()

    def report(c: Context) -> Response:
        return c.file(root / "report.csv", filename="売上 2026.csv")

    app.get("/report", report)

    def public(c: Context) -> Response:
        return c.file(c.req.param("name"), root=root / "public")

    app.get("/public/{name}", public)

    def raw(c: Context) -> Response:
        return c.file(root / "blob")

    app.get("/raw", raw)

    return app


def make_files(tmp_path: Path) -> Path:
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "hello.txt").write_text("hi")
    (tmp_path / "report.csv").write_text("a,b\n1,2\n")
    (tmp_path / "secret.txt").write_text("secret")
    (tmp_path / "blob").write_bytes(b"\x00\x01")
    return tmp_path


def test_a_file_is_sent_with_a_guessed_type_and_a_download_name(tmp_path: Path) -> None:
    with file_app(make_files(tmp_path)).test() as client:
        resp = client.get("/report")
    assert resp.body == b"a,b\n1,2\n"
    assert resp.headers["content-type"] == "text/csv"
    assert resp.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''%E5%A3%B2%E4%B8%8A%202026.csv"
    )


def test_a_file_larger_than_one_chunk_arrives_whole_and_in_order(tmp_path: Path) -> None:
    files = make_files(tmp_path)
    content = bytes(range(256)) * 1000  # 256,000 bytes: several 64 KiB chunks
    (files / "report.csv").write_bytes(content)
    with file_app(files).test() as client:
        resp = client.get("/report")
    assert resp.body == content


def test_an_unknown_extension_is_sent_as_octet_stream(tmp_path: Path) -> None:
    with file_app(make_files(tmp_path)).test() as client:
        resp = client.get("/raw")
    assert (resp.body, resp.headers["content-type"]) == (b"\x00\x01", "application/octet-stream")


def test_a_file_below_the_root_is_served_and_a_path_that_escapes_it_is_a_404(
    tmp_path: Path,
) -> None:
    with file_app(make_files(tmp_path)).test() as client:
        assert client.get("/public/hello.txt").text == "hi"
        assert client.get("/public/..%2Fsecret.txt").status == 404
        assert client.get("/public/missing.txt").status == 404


def test_head_on_a_file_route_sends_no_body(tmp_path: Path) -> None:
    with file_app(make_files(tmp_path)).test() as client:
        resp = client.request("HEAD", "/report")
    assert (resp.status, resp.body) == (200, b"")
    assert resp.headers["content-type"] == "text/csv"
