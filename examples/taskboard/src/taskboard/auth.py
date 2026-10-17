"""Sign up, log in, and who am I."""

import asyncio
import sqlite3
import time

from honoka_jwt import sign

from honoka import App, Context, HTTPError, Json, Middleware
from honoka_msgspec import validator
from taskboard import repo
from taskboard.deps import (
    DB,
    LOGIN_THROTTLE,
    REGISTER_THROTTLE,
    SETTINGS,
    client_host,
    current_user_id,
)
from taskboard.models import Credentials, ErrorBody, TokenOut, UserOut
from taskboard.security import DUMMY_HASH, hash_password, verify_password
from taskboard.throttle import Throttle

ISSUER = "taskboard"


def refuse_if_waiting(throttle: Throttle, key: str) -> None:
    """Answer 429 while `key` is over its limit. Called before any password is hashed, so a
    flood costs a dictionary lookup, not a scrypt run."""
    wait = throttle.retry_after(key)
    if wait is not None:
        raise HTTPError(429, "too_many_attempts", headers=[("retry-after", str(wait))])


async def register(c: Context) -> Json[UserOut]:
    """Create an account."""
    registrations, client = c.get(REGISTER_THROTTLE), client_host(c)
    refuse_if_waiting(registrations, client)
    registrations.hit(client)
    credentials = c.req.valid(Credentials)
    email = credentials.email.lower()
    password_hash = await asyncio.to_thread(hash_password, credentials.password)
    try:
        user = await c.get(DB).run(lambda conn: repo.create_user(conn, email, password_hash))
    except sqlite3.IntegrityError:
        raise HTTPError(409, "email_taken") from None
    return c.json(user, 201)


async def login(c: Context) -> Json[TokenOut]:
    """Exchange an email and password for an access token."""
    credentials = c.req.valid(Credentials)
    email = credentials.email.lower()
    attempts, key = c.get(LOGIN_THROTTLE), f"{client_host(c)}|{email}"
    refuse_if_waiting(attempts, key)
    row = await c.get(DB).run(lambda conn: repo.find_user(conn, email))
    stored = DUMMY_HASH if row is None else row["password_hash"]
    ok = await asyncio.to_thread(verify_password, credentials.password, stored)
    if row is None or not ok:
        attempts.hit(key)  # an unknown email counts too, so the limit tells nothing about accounts
        raise HTTPError(401, "invalid_credentials")
    attempts.reset(key)
    settings = c.get(SETTINGS)
    issued = int(time.time())
    token = sign(
        {"sub": str(row["id"]), "iss": ISSUER, "iat": issued, "exp": issued + settings.token_ttl},
        settings.secret,
    )
    return c.json(TokenOut(access_token=token, expires_in=settings.token_ttl))


async def me(c: Context) -> Json[UserOut]:
    """The signed-in user."""
    user_id = current_user_id(c)
    user = await c.get(DB).run(lambda conn: repo.get_user(conn, user_id))
    if user is None:  # a valid token for an account that no longer exists
        raise HTTPError(401, "invalid_token")
    return c.json(user)


def router(authenticate: Middleware) -> App:
    app = App()
    app.post(
        "/register",
        validator(json=Credentials),
        register,
        status=201,
        responses={409: (ErrorBody, "The email is already registered")},
    )
    app.post(
        "/login",
        validator(json=Credentials),
        login,
        responses={401: (ErrorBody, "Wrong email or password")},
    )
    app.get("/me", authenticate, me, responses={401: (ErrorBody, "Missing or invalid token")})
    return app
