"""Findings of the security scan: reused ids, streams that outlive their project or token,
unthrottled password guessing."""

import asyncio
import contextlib
import json
from dataclasses import replace

from taskboard_support import SignIn, body

from honoka import App
from honoka.asgi import AsgiMessage, AsgiScope
from honoka.testing import Client, asgi_call
from taskboard import Settings, create_app
from taskboard.throttle import Throttle

CREDENTIALS = {"email": "ann@example.com", "password": "correct horse battery"}


def test_a_deleted_projects_id_is_never_given_to_another_project(
    client: Client, sign_in: SignIn
) -> None:
    ann = sign_in("ann@example.com")
    first = body(client.post("/projects", json={"name": "a"}, headers=ann))["id"]
    client.delete(f"/projects/{first}", headers=ann)
    second = body(client.post("/projects", json={"name": "b"}, headers=ann))["id"]
    assert second != first


def test_repeated_wrong_passwords_are_refused_before_the_password_is_checked(
    client: Client, sign_in: SignIn
) -> None:
    sign_in("ann@example.com")
    wrong = {**CREDENTIALS, "password": "not the password"}
    answers = [client.post("/auth/login", json=wrong).status for _ in range(5)]
    assert answers == [401] * 5
    locked = client.post("/auth/login", json=CREDENTIALS)  # even the right password waits
    assert (locked.status, body(locked)["error"]) == (429, "too_many_attempts")
    assert int(locked.headers["retry-after"]) > 0


def test_a_lockout_is_per_client_and_email_so_other_accounts_still_log_in(
    client: Client, sign_in: SignIn
) -> None:
    sign_in("ann@example.com")
    sign_in("bob@example.com")
    wrong = {"email": "ann@example.com", "password": "not the password"}
    for _ in range(5):
        client.post("/auth/login", json=wrong)
    bob = client.post("/auth/login", json={**CREDENTIALS, "email": "bob@example.com"})
    assert bob.status == 200


def test_guessing_at_an_unknown_email_is_throttled_the_same_way(client: Client) -> None:
    ghost = {"email": "nobody@example.com", "password": "whatever it is"}
    statuses = [client.post("/auth/login", json=ghost).status for _ in range(6)]
    assert statuses == [401] * 5 + [429]


def test_a_successful_login_clears_the_failures(client: Client, sign_in: SignIn) -> None:
    sign_in("ann@example.com")
    wrong = {**CREDENTIALS, "password": "not the password"}
    for _ in range(4):
        client.post("/auth/login", json=wrong)
    assert client.post("/auth/login", json=CREDENTIALS).status == 200
    for _ in range(4):
        assert client.post("/auth/login", json=wrong).status == 401


def test_registrations_from_one_client_are_limited(settings: Settings) -> None:
    with create_app(replace(settings, register_attempts=3)).test() as client:
        statuses = [
            client.post(
                "/auth/register", json={**CREDENTIALS, "email": f"user{n}@example.com"}
            ).status
            for n in range(5)
        ]
    assert statuses == [201, 201, 201, 429, 429]


def test_the_throttle_forgets_attempts_after_its_window() -> None:
    now = [0.0]
    throttle = Throttle(2, 60, clock=lambda: now[0])
    throttle.hit("k")
    throttle.hit("k")
    assert throttle.retry_after("k") == 60
    now[0] = 59
    assert throttle.retry_after("k") == 1
    now[0] = 61
    assert throttle.retry_after("k") is None
    throttle.hit("k")
    throttle.reset("k")
    assert throttle.retry_after("k") is None


def test_the_throttle_keeps_a_bounded_number_of_keys() -> None:
    throttle = Throttle(1, 60, clock=lambda: 0.0, max_keys=3)
    for n in range(10):
        throttle.hit(f"k{n}")
    assert throttle.tracked() <= 3


async def open_stream(app: App, token: str, received: bytearray) -> asyncio.Task[None]:
    asked = False
    hang_up = asyncio.Event()

    async def receive() -> AsgiMessage:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await hang_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: AsgiMessage) -> None:
        chunk = message.get("body")
        if isinstance(chunk, bytes):
            received.extend(chunk)

    scope: AsgiScope = {
        "type": "http",
        "method": "GET",
        "path": "/projects/1/events",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(0.05)  # let it subscribe
    return task


async def sign_up(app: App) -> tuple[str, dict[str, str]]:
    json_type = {"content-type": "application/json"}
    payload = json.dumps(CREDENTIALS).encode()
    await asgi_call(app, "POST", "/auth/register", headers=json_type, content=payload)
    login = await asgi_call(app, "POST", "/auth/login", headers=json_type, content=payload)
    token = json.loads(login.text)["access_token"]
    auth = {"authorization": f"Bearer {token}", **json_type}
    await asgi_call(app, "POST", "/projects", headers=auth, content=b'{"name": "P"}')
    return token, auth


def test_deleting_a_project_ends_the_streams_watching_it(app: App) -> None:
    async def scenario() -> bool:
        await app.start()
        try:
            token, auth = await sign_up(app)
            received = bytearray()
            stream = await open_stream(app, token, received)
            await asgi_call(app, "DELETE", "/projects/1", headers=auth)
            done, _ = await asyncio.wait({stream}, timeout=2)
            if not done:
                stream.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream
            return bool(done)
        finally:
            await app.stop()

    assert asyncio.run(scenario()), "the stream was still open after its project was deleted"


def test_a_stream_ends_when_the_access_token_that_opened_it_expires(settings: Settings) -> None:
    app = create_app(replace(settings, token_ttl=1))

    async def scenario() -> bool:
        await app.start()
        try:
            token, _ = await sign_up(app)
            stream = await open_stream(app, token, bytearray())
            done, _ = await asyncio.wait({stream}, timeout=4)
            if not done:
                stream.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream
            return bool(done)
        finally:
            await app.stop()

    assert asyncio.run(scenario()), "the stream outlived its token"
