"""request_id."""

import re

import pytest
from honoka_request_id import REQUEST_ID, request_id

from honoka import App, Context, Response


def test_a_request_id_is_generated_exposed_to_handlers_and_echoed() -> None:
    app = App()
    app.use("*", request_id())

    @app.get("/")
    def handler(c: Context) -> Response:
        return c.text(c.get(REQUEST_ID))

    with app.test() as client:
        resp = client.get("/")

    assert re.fullmatch(r"[0-9a-f]{32}", resp.text)
    assert resp.headers["x-request-id"] == resp.text


def test_a_well_formed_incoming_id_is_reused() -> None:
    app = App()
    app.use("*", request_id())

    @app.get("/")
    def handler(c: Context) -> Response:
        return c.text(c.get(REQUEST_ID))

    with app.test() as client:
        resp = client.get("/", headers={"x-request-id": "trace-123.abc"})

    assert resp.text == resp.headers["x-request-id"] == "trace-123.abc"


@pytest.mark.parametrize("incoming", ["has space", "x" * 129, "semi;colon", ""])
def test_a_malformed_incoming_id_is_replaced(incoming: str) -> None:
    app = App()
    app.use("*", request_id())

    @app.get("/")
    def handler(c: Context) -> Response:
        return c.text("")

    with app.test() as client:
        resp = client.get("/", headers={"x-request-id": incoming})

    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["x-request-id"])
