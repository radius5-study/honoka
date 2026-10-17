"""secure_headers."""

import pytest
from honoka_secure_headers import secure_headers

from honoka import App, Context, Response


def test_secure_headers_adds_defaults_without_overriding_what_a_handler_set() -> None:
    app = App()
    app.use("*", secure_headers())

    def handler(c: Context) -> Response:
        response = c.text("")
        response.headers.append(("x-frame-options", "DENY"))
        return response

    app.get("/", handler)

    with app.test() as client:
        resp = client.get("/")

    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert [v for k, v in resp.raw_headers if k == "x-frame-options"] == ["DENY"]


def test_a_secure_header_can_be_replaced_or_disabled() -> None:
    app = App()
    app.use("*", secure_headers(referrer_policy="origin", x_frame_options=None))

    def handler(c: Context) -> Response:
        return c.text("")

    app.get("/", handler)

    with app.test() as client:
        resp = client.get("/")

    assert resp.headers["referrer-policy"] == "origin"
    assert "x-frame-options" not in resp.headers


def test_an_unknown_secure_header_name_is_a_typo_not_a_silent_no_op() -> None:
    with pytest.raises(TypeError, match="x_frame_option"):
        secure_headers(x_frame_option="DENY")
