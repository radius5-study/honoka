"""logger."""

import logging
import re

import pytest
from honoka_logger import logger
from honoka_request_id import request_id

from honoka import App, Context, HTTPError, Response


def test_logger_records_method_path_status_and_duration(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="honoka.access")
    app = App()
    app.use("*", logger())

    def hello(c: Context) -> Response:
        return c.text("")

    app.get("/hello", hello)

    def gone(c: Context) -> Response:
        raise HTTPError(410, "gone")

    app.get("/gone", gone)

    with app.test() as client:
        client.get("/hello")
        client.get("/gone")
        client.get("/nowhere")

    lines = [r.getMessage() for r in caplog.records if r.name == "honoka.access"]
    assert [re.sub(r"[\d.]+ms$", "Nms", line) for line in lines] == [
        "GET /hello 200 Nms",
        "GET /gone 410 Nms",
        "GET /nowhere 404 Nms",
    ]


def test_logger_includes_the_request_id_when_that_middleware_ran_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="honoka.access")
    app = App()
    app.use("*", request_id())
    app.use("*", logger())

    def handler(c: Context) -> Response:
        return c.text("")

    app.get("/", handler)

    with app.test() as client:
        client.get("/", headers={"x-request-id": "rid-1"})

    assert "rid=rid-1" in caplog.records[-1].getMessage()


def test_logger_escapes_control_characters_in_the_path(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="honoka.access")
    app = App()
    app.use("*", logger())

    with app.test() as client:
        client.get("/x\nFAKE 200")

    message = caplog.records[-1].getMessage()
    assert "\n" not in message
    assert "\\n" in message
