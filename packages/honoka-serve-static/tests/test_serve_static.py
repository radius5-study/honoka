"""serve_static."""

import gzip
from pathlib import Path

from honoka_serve_static import serve_static

from honoka import App, Context, Response


def site(tmp_path: Path) -> Path:
    root = tmp_path / "public"
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "hello.txt").write_text("hello")
    (root / "index.html").write_text("<h1>home</h1>")
    (root / "sub" / "index.html").write_text("sub")
    (root / "data.xyz").write_text("raw")
    (root / "app.js").write_text("1")
    (root / "app.js.gz").write_bytes(gzip.compress(b"1"))
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    return root


def test_serves_files_directories_and_head(tmp_path: Path) -> None:
    app = App()
    app.use("*", serve_static(root=site(tmp_path)))
    with app.test() as client:
        hello = client.get("/hello.txt")
        home = client.get("/")
        sub = client.get("/sub/")
        sub_bare = client.get("/sub")
        head = client.request("HEAD", "/hello.txt")
    assert (hello.status, hello.text) == (200, "hello")
    assert hello.headers["content-type"].startswith("text/plain")
    assert home.text == "<h1>home</h1>"
    assert sub.text == sub_bare.text == "sub"
    assert head.status == 200
    assert head.body == b""


def test_misses_and_other_methods_fall_through(tmp_path: Path) -> None:
    app = App()
    app.use("*", serve_static(root=site(tmp_path)))

    @app.get("/api")
    def api(c: Context) -> Response:
        return c.text("api")

    @app.post("/hello.txt")
    def post(c: Context) -> Response:
        return c.text("posted")

    with app.test() as client:
        api_resp = client.get("/api")
        missing = client.get("/nope.txt")
        posted = client.post("/hello.txt")
    assert api_resp.text == "api"
    assert missing.status == 404
    assert posted.text == "posted"


def test_traversal_never_leaves_root(tmp_path: Path) -> None:
    app = App()
    app.use("*", serve_static(root=site(tmp_path)))
    with app.test() as client:
        for target in (
            "/../secret.txt",
            "/%2e%2e/secret.txt",
            "/sub/../../secret.txt",
            "/%2e%2e%2fsecret.txt",
            f"/{tmp_path}/secret.txt",
            "//" + str(tmp_path / "secret.txt").lstrip("/"),
            "/hello.txt%00",
        ):
            resp = client.get(target)
            assert "TOP SECRET" not in resp.text, target
            assert resp.status == 404, target


def test_rewrite_path_and_single_file(tmp_path: Path) -> None:
    root = site(tmp_path)
    app = App()
    app.use(
        "/static/*", serve_static(root=root, rewrite_request_path=lambda p: p[len("/static") :])
    )
    app.use("/favicon.ico", serve_static(root=root, path="hello.txt"))
    with app.test() as client:
        assert client.get("/static/hello.txt").text == "hello"
        assert client.get("/favicon.ico").text == "hello"


def test_mimes_override_and_cache_control(tmp_path: Path) -> None:
    app = App()
    app.use("*", serve_static(root=site(tmp_path), mimes={"xyz": "application/x-xyz"}))
    plain = App()
    plain.use("*", serve_static(root=site(tmp_path), cache_control="max-age=60"))
    with app.test() as client, plain.test() as other:
        resp = client.get("/data.xyz")
        cached = other.get("/hello.txt")
        unset = client.get("/hello.txt")
    assert resp.headers["content-type"] == "application/x-xyz"
    assert cached.headers["cache-control"] == "max-age=60"
    assert "cache-control" not in unset.headers


def test_hooks(tmp_path: Path) -> None:
    seen: list[str] = []

    def found(path: str, c: Context) -> None:
        seen.append("found " + path)
        c.header("x-served", "1")

    async def missing(path: str, c: Context) -> None:
        seen.append("missing " + path)

    app = App()
    app.use("*", serve_static(root=site(tmp_path), on_found=found, on_not_found=missing))
    with app.test() as client:
        ok = client.get("/hello.txt")
        client.get("/nope")
    assert ok.headers["x-served"] == "1"
    assert seen == ["found hello.txt", "missing nope"]


def test_precompressed(tmp_path: Path) -> None:
    app = App()
    app.use("*", serve_static(root=site(tmp_path), precompressed=True))
    with app.test() as client:
        zipped = client.get("/app.js", headers={"accept-encoding": "gzip, deflate"})
        plain = client.get("/app.js")
        no_variant = client.get("/hello.txt", headers={"accept-encoding": "gzip"})
    assert zipped.headers["content-encoding"] == "gzip"
    assert zipped.headers["content-type"].endswith("javascript")
    assert gzip.decompress(zipped.body) == b"1"
    assert plain.body == b"1"
    assert "content-encoding" not in plain.headers
    assert no_variant.text == "hello"
