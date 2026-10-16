"""cors."""

import pytest
from honoka_cors import cors

from honoka import App, Context, HTTPError, Response


def cors_app(**options: object) -> App:
    app = App()
    app.use("*", cors(**options))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    @app.get("/data")
    def data(c: Context) -> Response:
        return c.text("data")

    @app.get("/forbidden")
    def forbidden(c: Context) -> Response:
        raise HTTPError(403, "forbidden")

    return app


def test_an_allowed_origin_is_echoed_with_vary_origin() -> None:
    with cors_app(origins=["https://a.test"]).test() as client:
        resp = client.get("/data", headers={"origin": "https://a.test"})
    assert resp.headers["access-control-allow-origin"] == "https://a.test"
    assert resp.headers["vary"] == "Origin"


def test_a_disallowed_origin_gets_no_cors_headers() -> None:
    with cors_app(origins=["https://a.test"]).test() as client:
        resp = client.get("/data", headers={"origin": "https://evil.test"})
    assert "access-control-allow-origin" not in resp.headers


def test_requests_without_an_origin_are_untouched() -> None:
    with cors_app(origins=["https://a.test"]).test() as client:
        assert "access-control-allow-origin" not in client.get("/data").headers


def test_a_wildcard_origin_answers_star_without_vary() -> None:
    with cors_app(origins=["*"]).test() as client:
        resp = client.get("/data", headers={"origin": "https://any.test"})
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "vary" not in resp.headers


def test_a_preflight_is_answered_before_routing_with_methods_headers_and_max_age() -> None:
    app = cors_app(origins=["https://a.test"], max_age=600)
    with app.test() as client:
        resp = client.request(
            "OPTIONS",
            "/anything",
            headers={
                "origin": "https://a.test",
                "access-control-request-method": "POST",
                "access-control-request-headers": "x-a, content-type",
            },
        )
    assert resp.status == 204
    assert resp.headers["access-control-allow-origin"] == "https://a.test"
    assert resp.headers["access-control-allow-methods"] == "GET, HEAD, PUT, POST, DELETE, PATCH"
    assert resp.headers["access-control-allow-headers"] == "x-a, content-type"
    assert resp.headers["access-control-max-age"] == "600"


def test_credentials_are_allowed_only_for_explicit_origins() -> None:
    with cors_app(origins=["https://a.test"], credentials=True).test() as client:
        resp = client.get("/data", headers={"origin": "https://a.test"})
    assert resp.headers["access-control-allow-credentials"] == "true"
    with pytest.raises(ValueError, match="credentials"):
        cors(origins=["*"], credentials=True)


def test_error_responses_still_carry_cors_headers() -> None:
    with cors_app(origins=["https://a.test"]).test() as client:
        resp = client.get("/forbidden", headers={"origin": "https://a.test"})
    assert resp.status == 403
    assert resp.headers["access-control-allow-origin"] == "https://a.test"
