"""Cookie building and signing (Hono's cookie helper), with no dependency on the Context."""

import base64
import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Literal

_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_COOKIE_ATTR = re.compile(r"[\x21\x23-\x3a\x3c-\x7e]+")
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_MAX_AGE_LIMIT = int(timedelta(days=400).total_seconds())

type Prefix = Literal["secure", "host"]
type SameSite = Literal["Lax", "Strict", "None"]
type Priority = Literal["Low", "Medium", "High"]


def cookie_name(name: str, prefix: Prefix | None) -> str:
    if prefix == "secure":
        return f"__Secure-{name}"
    if prefix == "host":
        return f"__Host-{name}"
    return name


def generate_cookie(
    name: str,
    value: str,
    *,
    max_age: int | None = None,
    expires: datetime | None = None,
    path: str = "/",
    domain: str | None = None,
    secure: bool = False,
    http_only: bool = True,
    same_site: SameSite | None = "Lax",
    priority: Priority | None = None,
    partitioned: bool = False,
    prefix: Prefix | None = None,
) -> str:
    """A `Set-Cookie` header value. Defaults are safe: HttpOnly and SameSite=Lax."""
    if not _COOKIE_NAME.fullmatch(name) or not _COOKIE_VALUE.fullmatch(value):
        raise ValueError("cookie name or value contains characters that are not allowed")
    if not _COOKIE_ATTR.fullmatch(path) or (
        domain is not None and not _COOKIE_ATTR.fullmatch(domain)
    ):
        raise ValueError("cookie path or domain contains characters that are not allowed")
    if prefix is not None:
        secure = True  # both prefixes require Secure
        if prefix == "host" and (path != "/" or domain is not None):
            raise ValueError('a "__Host-" cookie needs path "/" and no domain')
        name = cookie_name(name, prefix)
    if max_age is not None and max_age > _MAX_AGE_LIMIT:
        raise ValueError("max_age must be at most 400 days")
    if expires is not None:
        expires = expires.replace(tzinfo=UTC) if expires.tzinfo is None else expires.astimezone(UTC)
        if expires - datetime.now(UTC) > timedelta(days=400):
            raise ValueError("expires must be at most 400 days ahead")
    if partitioned:
        secure = True
    parts = [f"{name}={value}", f"Path={path}"]
    if domain is not None:
        parts.append(f"Domain={domain}")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if expires is not None:
        parts.append(f"Expires={format_datetime(expires, usegmt=True)}")
    if secure or same_site == "None":
        parts.append("Secure")
    if http_only:
        parts.append("HttpOnly")
    if same_site is not None:
        parts.append(f"SameSite={same_site}")
    if priority is not None:
        parts.append(f"Priority={priority}")
    if partitioned:
        parts.append("Partitioned")
    return "; ".join(parts)


def _signature(value: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign(name: str, value: str, secret: str) -> str:
    """`value.signature`. The signature covers the cookie's name too, so a value signed for one
    cookie is not accepted as another."""
    return f"{value}.{_signature(f'{name}={value}', secret)}"


def unsign(name: str, signed: str, secret: str) -> str | None:
    """The value of a string made by `sign` for this cookie, or `None` if it does not verify."""
    value, dot, signature = signed.rpartition(".")
    expected = _signature(f"{name}={value}", secret)
    if not dot or not hmac.compare_digest(signature.encode(), expected.encode()):
        return None
    return value
