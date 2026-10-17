"""basic_auth."""

import base64
import hmac

import pytest
from honoka_basic_auth import USERNAME, BasicUser, basic_auth

from honoka import App, Context, Middleware, Response


def creds(user: str, password: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def make(middleware: Middleware) -> App:
    app = App()
    app.use("*", middleware)

    def index(c: Context) -> Response:
        return c.text("hello " + c.get(USERNAME))

    app.get("/", index)

    return app


def test_valid_credentials_reach_the_handler_with_the_username() -> None:
    with make(basic_auth(username="admin", password="s3cret")).test() as client:
        resp = client.get("/", headers=creds("admin", "s3cret"))
    assert (resp.status, resp.text) == (200, "hello admin")


def test_missing_wrong_or_malformed_credentials_get_a_401_challenge() -> None:
    bad = [
        {},
        creds("admin", "wrong"),
        creds("other", "s3cret"),
        {"authorization": "Basic !!!not-base64"},
        {"authorization": "Bearer abc"},
        {"authorization": "Basic " + base64.b64encode(b"nocolon").decode()},
    ]
    with make(basic_auth(username="admin", password="s3cret")).test() as client:
        for headers in bad:
            resp = client.get("/", headers=headers)
            assert resp.status == 401
            assert resp.headers["www-authenticate"] == 'Basic realm="Secure Area"'
            assert resp.json() == {"error": "unauthorized"}


def test_password_may_contain_colons_and_unicode() -> None:
    with make(basic_auth(username="u", password="a:b:日本")).test() as client:
        assert client.get("/", headers=creds("u", "a:b:日本")).status == 200


def test_realm_is_configurable() -> None:
    with make(basic_auth(username="a", password="b", realm="Staff")).test() as client:
        resp = client.get("/")
    assert resp.headers["www-authenticate"] == 'Basic realm="Staff"'


def test_several_users_are_accepted() -> None:
    mw = basic_auth(users=[BasicUser("a", "1"), BasicUser("b", "2")])
    with make(mw).test() as client:
        assert client.get("/", headers=creds("b", "2")).text == "hello b"
        assert client.get("/", headers=creds("a", "2")).status == 401


def test_verify_user_may_be_sync_or_async() -> None:
    def sync(user: str, password: str, c: Context) -> bool:
        return password == "pw"

    async def aync(user: str, password: str, c: Context) -> bool:
        return user == "bob"

    with make(basic_auth(verify_user=sync)).test() as client:
        assert client.get("/", headers=creds("x", "pw")).status == 200
        assert client.get("/", headers=creds("x", "no")).status == 401
    with make(basic_auth(verify_user=aync)).test() as client:
        assert client.get("/", headers=creds("bob", "")).status == 200
        assert client.get("/", headers=creds("eve", "")).status == 401


def test_invalid_user_message_replaces_the_body_keeping_the_challenge() -> None:
    with make(basic_auth(username="a", password="b", invalid_user_message="go away")).test() as c1:
        resp = c1.get("/")
    assert (resp.status, resp.text) == (401, "go away")
    assert resp.headers["www-authenticate"].startswith("Basic")
    mw = basic_auth(username="a", password="b", invalid_user_message=lambda c: {"msg": "no"})
    with make(mw).test() as c2:
        assert c2.get("/").json() == {"msg": "no"}


def test_on_auth_success_runs_after_authentication() -> None:
    seen: list[str] = []
    mw = basic_auth(username="a", password="b", on_auth_success=lambda c, u: seen.append(u))
    with make(mw).test() as client:
        client.get("/", headers=creds("a", "b"))
        client.get("/", headers=creds("a", "x"))
    assert seen == ["a"]


def test_credentials_are_compared_with_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    with make(basic_auth(username="a", password="b")).test() as client:
        client.get("/", headers=creds("a", "b"))
    assert len(calls) == 2
