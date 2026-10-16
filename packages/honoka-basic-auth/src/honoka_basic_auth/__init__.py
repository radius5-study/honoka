"""HTTP Basic authentication middleware (port of Hono's `basicAuth`)."""

import base64
import binascii
import hashlib
import hmac
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import NamedTuple

from honoka import Context, HTTPError, Key, Middleware, Next, Response

__all__ = ["USERNAME", "BasicUser", "basic_auth"]

USERNAME: Key[str] = Key("basic_auth_username")
"""The authenticated username, set before the handler runs."""

type Message = str | Mapping[str, object] | Callable[[Context], str | Mapping[str, object]]
type VerifyUser = Callable[[str, str, Context], bool | Awaitable[bool]]
type OnSuccess = Callable[[Context, str], object]

_CREDENTIALS = re.compile(r"^ *basic +([A-Za-z0-9._~+/-]+=*) *$", re.IGNORECASE)


class BasicUser(NamedTuple):
    username: str
    password: str


def _digest(value: str) -> bytes:
    # Hashing first gives compare_digest equal-length inputs, so length does not leak.
    return hashlib.sha256(value.encode()).digest()


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(_digest(a), _digest(b))


def _parse(header: str | None) -> BasicUser | None:
    match = _CREDENTIALS.match(header) if header is not None else None
    if match is None:
        return None
    try:
        decoded = base64.b64decode(match.group(1), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    return BasicUser(username, password) if sep else None


def _resolve(c: Context, message: Message) -> str | Mapping[str, object]:
    return message if isinstance(message, str | Mapping) else message(c)


def basic_auth(
    *,
    username: str | None = None,
    password: str | None = None,
    users: Sequence[BasicUser] = (),
    realm: str = "Secure Area",
    verify_user: VerifyUser | None = None,
    invalid_user_message: Message | None = None,
    on_auth_success: OnSuccess | None = None,
) -> Middleware:
    """Require `Authorization: Basic ...` matching `username`/`password`, any of `users`, or
    `verify_user(username, password, c)` (sync or async). Credentials are compared in constant
    time. Failure answers 401 with `WWW-Authenticate: Basic realm="..."`; `invalid_user_message`
    (str, mapping for JSON, or a function of the context) replaces the default JSON body."""
    accepted = list(users)
    if username is not None and password is not None:
        accepted.insert(0, BasicUser(username, password))
    if not accepted and verify_user is None:
        raise ValueError("basic_auth needs username/password, users or verify_user")
    challenge: dict[str, str] = {"www-authenticate": f'Basic realm="{realm}"'}

    async def middleware(c: Context, call_next: Next) -> Response:
        creds = _parse(c.req.header("authorization"))
        ok = False
        if creds is not None:
            for user in accepted:  # no early exit: every user is compared
                matched = _equal(creds.username, user.username)
                matched = _equal(creds.password, user.password) and matched
                ok = ok or matched
            if not ok and verify_user is not None:
                result = verify_user(creds.username, creds.password, c)
                ok = await result if inspect.isawaitable(result) else result
        if creds is None or not ok:
            if invalid_user_message is None:
                raise HTTPError(401, "unauthorized", headers=list(challenge.items()))
            msg = (
                invalid_user_message(c) if callable(invalid_user_message) else invalid_user_message
            )
            if isinstance(msg, str):
                return c.text(msg, 401, challenge)
            return c.json(msg, 401, challenge)
        c.set(USERNAME, creds.username)
        if on_auth_success is not None:
            hook = on_auth_success(c, creds.username)
            if inspect.isawaitable(hook):
                await hook
        return await call_next()

    return middleware
