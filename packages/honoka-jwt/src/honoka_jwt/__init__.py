"""JWT sign/verify/decode and an authentication middleware (port of Hono's `hono/jwt`).

Only the HMAC algorithms HS256, HS384 and HS512 are implemented, using the standard library.
RS*/PS*/ES*/EdDSA need a crypto library and are intentionally not supported.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import Literal

from honoka import Context, HTTPError, Key, Middleware, Next, Response

__all__ = [
    "JWT_PAYLOAD",
    "Alg",
    "JwtAlgorithmNotImplemented",
    "JwtError",
    "JwtPayloadRequiresAud",
    "JwtTokenAudience",
    "JwtTokenExpired",
    "JwtTokenInvalid",
    "JwtTokenIssuedAt",
    "JwtTokenIssuer",
    "JwtTokenNotBefore",
    "JwtTokenSignatureMismatched",
    "decode",
    "jwt",
    "sign",
    "verify",
]

type Alg = Literal["HS256", "HS384", "HS512"]
type Payload = dict[str, object]
type Matcher = str | re.Pattern[str]

JWT_PAYLOAD: Key[Payload] = Key("jwtPayload")
"""Default slot for the verified payload."""

_DIGESTS = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


class JwtError(Exception):
    """Base of every JWT failure."""


class JwtAlgorithmNotImplemented(JwtError): ...


class JwtTokenInvalid(JwtError): ...


class JwtTokenNotBefore(JwtError): ...


class JwtTokenExpired(JwtError): ...


class JwtTokenIssuedAt(JwtError): ...


class JwtTokenIssuer(JwtError): ...


class JwtPayloadRequiresAud(JwtError): ...


class JwtTokenAudience(JwtError): ...


class JwtTokenSignatureMismatched(JwtError): ...


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise JwtTokenInvalid("malformed base64url") from exc


def _json_object(data: bytes) -> Payload:
    try:
        value: object = json.loads(data)
    except ValueError as exc:
        raise JwtTokenInvalid("malformed JSON") from exc
    if not isinstance(value, dict):
        raise JwtTokenInvalid("expected a JSON object")
    return {str(k): v for k, v in value.items()}


def _mac(alg: str, secret: str, signing_input: str) -> bytes:
    digest = _DIGESTS.get(alg)
    if digest is None:
        raise JwtAlgorithmNotImplemented(f"{alg} is not implemented (HS256/384/512 only)")
    return hmac.digest(secret.encode(), signing_input.encode(), digest)


def sign(payload: Mapping[str, object], secret: str, alg: Alg = "HS256") -> str:
    header = _b64e(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}"
    return f"{signing_input}.{_b64e(_mac(alg, secret, signing_input))}"


def decode(token: str) -> tuple[Payload, Payload]:
    """`(header, payload)` without verifying anything."""
    parts = token.split(".")
    if len(parts) != 3:
        raise JwtTokenInvalid("a token has three parts")
    return _json_object(_b64d(parts[0])), _json_object(_b64d(parts[1]))


def _number(payload: Payload, claim: str) -> float | None:
    value = payload.get(claim)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JwtTokenInvalid(f"{claim} must be a number")
    return value


def _matches(expected: Matcher, actual: str) -> bool:
    if isinstance(expected, str):
        return expected == actual
    return expected.search(actual) is not None


def verify(
    token: str,
    secret: str,
    alg: Alg = "HS256",
    *,
    iss: Matcher | None = None,
    aud: Matcher | Sequence[str] | None = None,
    nbf: bool = True,
    iat: bool = True,
    exp: bool = True,
) -> Payload:
    """Check the signature (against `alg` only), then exp, nbf, iat, and `iss`/`aud` if given."""
    header, payload = decode(token)
    if header.get("alg") != alg:
        raise JwtTokenInvalid("header alg does not match the expected algorithm")
    signing_input, _, signature = token.rpartition(".")
    if not hmac.compare_digest(_mac(alg, secret, signing_input), _b64d(signature)):
        raise JwtTokenSignatureMismatched("signature mismatch")
    now = time.time()
    not_before, issued_at, expires = (_number(payload, k) for k in ("nbf", "iat", "exp"))
    if nbf and not_before is not None and now < not_before:
        raise JwtTokenNotBefore("token is not yet valid")
    if exp and expires is not None and now >= expires:
        raise JwtTokenExpired("token expired")
    if iat and issued_at is not None and now < issued_at:
        raise JwtTokenIssuedAt("token issued in the future")
    if iss is not None:
        issuer = payload.get("iss")
        if not isinstance(issuer, str) or not _matches(iss, issuer):
            raise JwtTokenIssuer("unexpected issuer")
    if aud is not None:
        claim = payload.get("aud")
        if claim is None:
            raise JwtPayloadRequiresAud("payload has no aud claim")
        claimed = [claim] if isinstance(claim, str) else claim
        if not isinstance(claimed, list) or not all(isinstance(a, str) for a in claimed):
            raise JwtTokenInvalid("aud must be a string or a list of strings")
        wanted = [aud] if isinstance(aud, str | re.Pattern) else list(aud)
        if not any(_matches(w, str(a)) for w in wanted for a in claimed):
            raise JwtTokenAudience("unexpected audience")
    return payload


def jwt(
    *,
    secret: str,
    alg: Alg = "HS256",
    cookie: str | None = None,
    header_name: str = "authorization",
    realm: str | None = None,
    key: Key[Payload] = JWT_PAYLOAD,
    iss: Matcher | None = None,
    aud: Matcher | Sequence[str] | None = None,
    nbf: bool = True,
    iat: bool = True,
    exp: bool = True,
) -> Middleware:
    """Require a valid JWT, from the `cookie` if given, else `header_name` (`Bearer <token>`;
    any scheme is accepted, as in Hono). The payload is stored under `key`. Failures answer 401
    with a `WWW-Authenticate: Bearer` challenge (realm defaults to the request path)."""

    async def middleware(c: Context, call_next: Next) -> Response:
        challenge = f'Bearer realm="{c.req.path if realm is None else realm}"'
        token: str | None
        if cookie is not None:
            token = c.req.cookie(cookie)
        else:
            parts = (c.req.header(header_name) or "").split(" ")
            token = parts[1] if len(parts) == 2 and parts[1] else None
        if token is None:
            raise HTTPError(401, "unauthorized", headers=[("www-authenticate", challenge)])
        try:
            payload = verify(token, secret, alg, iss=iss, aud=aud, nbf=nbf, iat=iat, exp=exp)
        except JwtError as exc:
            invalid = (
                f'{challenge},error="invalid_token",error_description="token verification failure"'
            )
            raise HTTPError(
                401, "invalid_token", type(exc).__name__, headers=[("www-authenticate", invalid)]
            ) from exc
        c.set(key, payload)
        return await call_next()

    return middleware
