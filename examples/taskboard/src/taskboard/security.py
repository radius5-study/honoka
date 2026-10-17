"""Password hashing with scrypt from the standard library."""

import base64
import hashlib
import hmac
import os

_N, _R, _P = 2**14, 8, 1


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(_derive(password, salt)).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        return hmac.compare_digest(
            _derive(password, base64.b64decode(salt)), base64.b64decode(digest)
        )
    except ValueError:
        return False


# Verified when the email is unknown, so a login takes the same time either way.
DUMMY_HASH = hash_password("not-a-real-password")
