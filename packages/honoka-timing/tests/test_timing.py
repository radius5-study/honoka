"""timing."""

import re

from honoka_timing import timing

from honoka import App, Context, Response


def test_timing_adds_a_server_timing_header() -> None:
    app = App()
    app.use("*", timing())

    @app.get("/")
    def handler(c: Context) -> Response:
        return c.text("")

    with app.test() as client:
        resp = client.get("/")

    assert re.fullmatch(r"app;dur=\d+(\.\d+)?", resp.headers["server-timing"])
