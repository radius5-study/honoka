from honoka_powered_by import powered_by

from honoka import App, Context, Response


def _app(name: str | None = None) -> App:
    app = App()
    app.use("*", powered_by() if name is None else powered_by(name))

    @app.get("/")
    def handler(c: Context) -> Response:
        return c.text("ok")

    return app


def test_default_name() -> None:
    with _app().test() as client:
        resp = client.get("/")
    assert resp.text == "ok"
    assert resp.headers["x-powered-by"] == "honoka"


def test_custom_name_and_unmatched_route() -> None:
    with _app("Sakura").test() as client:
        found = client.get("/")
        missing = client.get("/nope")
    assert found.headers["x-powered-by"] == "Sakura"
    assert missing.status == 404
    assert missing.headers["x-powered-by"] == "Sakura"
