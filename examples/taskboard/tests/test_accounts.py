"""Sign up, log in, and what a token has to be to get in."""

import time

from honoka_jwt import sign
from taskboard_support import SECRET, SignIn, body

from honoka.testing import Client

CREDENTIALS = {"email": "Ann@Example.com", "password": "correct horse battery"}


def test_registering_returns_the_user_without_any_password_and_lowercases_the_email(
    client: Client,
) -> None:
    resp = client.post("/auth/register", json=CREDENTIALS)
    assert (resp.status, resp.json()) == (201, {"id": 1, "email": "ann@example.com"})


def test_the_same_email_cannot_register_twice_whatever_its_case(client: Client) -> None:
    client.post("/auth/register", json=CREDENTIALS)
    again = client.post("/auth/register", json={**CREDENTIALS, "email": "ANN@example.com"})
    assert (again.status, again.json()) == (409, {"error": "email_taken"})


def test_a_short_password_or_a_bad_email_names_the_field(client: Client) -> None:
    short = client.post("/auth/register", json={**CREDENTIALS, "password": "short"})
    assert short.status == 400
    assert body(short)["fields"] == [
        {"in": "json", "path": "password", "expected": "str of length >= 8", "received": "short"}
    ]
    assert client.post("/auth/register", json={**CREDENTIALS, "email": "nope"}).status == 400


def test_login_gives_a_token_that_opens_the_protected_routes(client: Client) -> None:
    client.post("/auth/register", json=CREDENTIALS)
    token = body(client.post("/auth/login", json=CREDENTIALS))
    assert (token["token_type"], token["expires_in"]) == ("bearer", 3600)
    me = client.get("/auth/me", headers={"authorization": f"Bearer {token['access_token']}"})
    assert (me.status, me.json()) == (200, {"id": 1, "email": "ann@example.com"})


def test_a_wrong_password_and_an_unknown_email_look_the_same(client: Client) -> None:
    client.post("/auth/register", json=CREDENTIALS)
    wrong = client.post("/auth/login", json={**CREDENTIALS, "password": "wrong password"})
    unknown = client.post("/auth/login", json={**CREDENTIALS, "email": "who@example.com"})
    assert (wrong.status, wrong.json()) == (401, {"error": "invalid_credentials"})
    assert (unknown.status, unknown.json()) == (wrong.status, wrong.json())


def test_protected_routes_refuse_a_missing_or_unusable_token(
    client: Client, sign_in: SignIn
) -> None:
    sign_in("ann@example.com")
    now = int(time.time())
    expired = sign({"sub": "1", "iss": "taskboard", "exp": now - 10}, SECRET)
    other_key = sign({"sub": "1", "iss": "taskboard", "exp": now + 600}, "z" * 40)
    other_issuer = sign({"sub": "1", "iss": "someone-else", "exp": now + 600}, SECRET)
    no_subject = sign({"iss": "taskboard", "exp": now + 600}, SECRET)
    for header in (
        {},
        {"authorization": "Bearer garbage"},
        {"authorization": f"Bearer {expired}"},
        {"authorization": f"Bearer {other_key}"},
        {"authorization": f"Bearer {other_issuer}"},
        {"authorization": f"Bearer {no_subject}"},
    ):
        for path in ("/auth/me", "/projects", "/projects/1/tasks"):
            assert client.get(path, headers=header).status == 401, (path, header)


def test_a_token_for_a_deleted_account_is_refused(client: Client) -> None:
    ghost = sign({"sub": "99", "iss": "taskboard", "exp": int(time.time()) + 600}, SECRET)
    assert client.get("/auth/me", headers={"authorization": f"Bearer {ghost}"}).status == 401
