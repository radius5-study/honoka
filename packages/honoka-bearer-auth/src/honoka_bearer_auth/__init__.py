"""Bearer token authentication middleware (port of Hono's `bearerAuth`)."""

import hashlib
import hmac
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from honoka import Context, HTTPError, Middleware, Next, Response

__all__ = ["Reply", "bearer_auth"]

type Message = str | Mapping[str, object] | Callable[[Context], str | Mapping[str, object]]
type Challenge = str | Callable[[Context], str]
type VerifyToken = Callable[[str, Context], bool | Awaitable[bool]]

_TOKEN = r"[A-Za-z0-9._~+/-]+=*"


@dataclass(frozen=True, slots=True)
class Reply:
    """Overrides for one failure: the `WWW-Authenticate` value and/or the response body."""

    www_authenticate: Challenge | None = None
    message: Message | None = None


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest()
    )


def bearer_auth(
    *,
    token: str | Sequence[str] = (),
    verify_token: VerifyToken | None = None,
    realm: str = "",
    prefix: str = "Bearer",
    header_name: str = "authorization",
    no_authentication_header: Reply | None = None,
    invalid_authentication_header: Reply | None = None,
    invalid_token: Reply | None = None,
) -> Middleware:
    """Require `Authorization: Bearer <token>` where the token is one of `token` (compared in
    constant time) or accepted by `verify_token(token, c)` (sync or async).

    Failures follow Hono: missing header 401, malformed header 400, wrong token 401, each with a
    `WWW-Authenticate` challenge. A `Reply` overrides the challenge and/or body of one failure
    (the challenge may be a string or a function of the context; Hono's object form is not
    supported)."""
    tokens = [token] if isinstance(token, str) else list(token)
    if not tokens and verify_token is None:
        raise ValueError("bearer_auth needs token or verify_token")
    pattern = re.compile(rf"^{re.escape(prefix)} +({_TOKEN}) *$")
    base = f'{prefix} realm="{realm}"'

    def reject(c: Context, status: int, code: str, challenge: str, reply: Reply | None) -> Response:
        value = challenge
        if reply is not None and reply.www_authenticate is not None:
            wa = reply.www_authenticate
            value = wa if isinstance(wa, str) else wa(c)
        headers: dict[str, str] = {"www-authenticate": value}
        if reply is None or reply.message is None:
            raise HTTPError(status, code, headers=list(headers.items()))
        msg = reply.message if isinstance(reply.message, str | Mapping) else reply.message(c)
        if isinstance(msg, str):
            return c.text(msg, status, headers)
        return c.json(msg, status, headers)

    async def middleware(c: Context, call_next: Next) -> Response:
        header = c.req.header(header_name)
        if header is None:
            return reject(c, 401, "unauthorized", base, no_authentication_header)
        match = pattern.match(header)
        if match is None:
            challenge = (
                f'{base},error="invalid_request",error_description="invalid credentials structure"'
            )
            return reject(c, 400, "bad_request", challenge, invalid_authentication_header)
        given = match.group(1)
        ok = False
        for t in tokens:  # no early exit: every token is compared
            ok = _equal(given, t) or ok
        if not ok and verify_token is not None:
            result = verify_token(given, c)
            ok = await result if inspect.isawaitable(result) else result
        if not ok:
            challenge = (
                f'{base},error="invalid_token",error_description="token verification failure"'
            )
            return reject(c, 401, "unauthorized", challenge, invalid_token)
        return await call_next()

    return middleware
