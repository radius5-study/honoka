"""bearer_auth."""

import hmac

import pytest
from honoka_bearer_auth import Reply, bearer_auth

from honoka import App, Context, Middleware, Response


def make(middleware: Middleware) -> App:
    app = App()
    app.use("*", middleware)

    @app.get("/")
    def index(c: Context) -> Response:
        return c.text("ok")

    return app


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_a_matching_token_is_accepted() -> None:
    with make(bearer_auth(token="honoiscool")).test() as client:
        assert client.get("/", headers=bearer("honoiscool")).text == "ok"


def test_any_of_several_tokens_is_accepted() -> None:
    with make(bearer_auth(token=["a1", "b2"])).test() as client:
        assert client.get("/", headers=bearer("b2")).status == 200
        assert client.get("/", headers=bearer("c3")).status == 401


def test_missing_header_is_401_with_a_challenge() -> None:
    with make(bearer_auth(token="t", realm="api")).test() as client:
        resp = client.get("/")
    assert resp.status == 401
    assert resp.headers["www-authenticate"] == 'Bearer realm="api"'


def test_malformed_header_is_400_invalid_request() -> None:
    with make(bearer_auth(token="t")).test() as client:
        for value in ("t", "Basic t", "Bearer", "Bearer a b", "Bearer @@"):
            resp = client.get("/", headers={"authorization": value})
            assert resp.status == 400
            assert 'error="invalid_request"' in resp.headers["www-authenticate"]


def test_wrong_token_is_401_invalid_token() -> None:
    with make(bearer_auth(token="t")).test() as client:
        resp = client.get("/", headers=bearer("nope"))
    assert resp.status == 401
    assert 'error="invalid_token"' in resp.headers["www-authenticate"]


def test_verify_token_sync_and_async() -> None:
    def sync(token: str, c: Context) -> bool:
        return token.startswith("ok")

    async def aync(token: str, c: Context) -> bool:
        return token == "later"

    with make(bearer_auth(verify_token=sync)).test() as client:
        assert client.get("/", headers=bearer("ok-1")).status == 200
        assert client.get("/", headers=bearer("bad")).status == 401
    with make(bearer_auth(verify_token=aync)).test() as client:
        assert client.get("/", headers=bearer("later")).status == 200


def test_custom_prefix_and_header_name() -> None:
    mw = bearer_auth(token="t", prefix="Token", header_name="x-auth")
    with make(mw).test() as client:
        assert client.get("/", headers={"x-auth": "Token t"}).status == 200
        assert client.get("/", headers={"x-auth": "Bearer t"}).status == 400


def test_custom_replies_override_body_and_challenge() -> None:
    mw = bearer_auth(
        token="t",
        no_authentication_header=Reply("Bearer x", "login first"),
        invalid_authentication_header=Reply(message={"e": "shape"}),
        invalid_token=Reply(message=lambda c: "bad token"),
    )
    with make(mw).test() as client:
        none = client.get("/")
        shape = client.get("/", headers={"authorization": "junk"})
        bad = client.get("/", headers=bearer("zzz"))
    assert (none.status, none.text, none.headers["www-authenticate"]) == (
        401,
        "login first",
        "Bearer x",
    )
    assert (shape.status, shape.json()) == (400, {"e": "shape"})
    assert (bad.status, bad.text) == (401, "bad token")


def test_tokens_are_compared_with_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    with make(bearer_auth(token=["a", "b"])).test() as client:
        client.get("/", headers=bearer("a"))
    assert len(calls) == 2
