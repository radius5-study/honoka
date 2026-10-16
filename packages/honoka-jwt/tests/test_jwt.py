"""jwt sign/verify/decode and the middleware."""

import base64
import re
import time
from typing import cast

import pytest
from honoka_jwt import (
    JWT_PAYLOAD,
    Alg,
    JwtAlgorithmNotImplemented,
    JwtError,
    JwtPayloadRequiresAud,
    JwtTokenAudience,
    JwtTokenExpired,
    JwtTokenInvalid,
    JwtTokenIssuedAt,
    JwtTokenIssuer,
    JwtTokenNotBefore,
    JwtTokenSignatureMismatched,
    decode,
    jwt,
    sign,
    verify,
)

from honoka import App, Context, Key, Middleware, Response

SECRET = "s3cret"


def future(seconds: int = 3600) -> int:
    return int(time.time()) + seconds


def test_sign_then_verify_round_trips_for_each_algorithm() -> None:
    for alg in ("HS256", "HS384", "HS512"):
        token = sign({"sub": "u1", "n": 1}, SECRET, alg)
        assert verify(token, SECRET, alg) == {"sub": "u1", "n": 1}
        header, payload = decode(token)
        assert (header, payload["sub"]) == ({"alg": alg, "typ": "JWT"}, "u1")


def test_decode_does_not_check_the_signature_and_rejects_garbage() -> None:
    assert decode(sign({"a": 1}, "other"))[1] == {"a": 1}
    for bad in ("", "a.b", "a.b.c.d", "!!.!!.!!"):
        with pytest.raises(JwtTokenInvalid):
            decode(bad)


def test_bad_signature_wrong_secret_and_tampering_are_rejected() -> None:
    token = sign({"sub": "a"}, SECRET)
    with pytest.raises(JwtTokenSignatureMismatched):
        verify(token, "wrong")
    head, _body, sig = token.split(".")
    forged = sign({"sub": "admin"}, "wrong").split(".")[1]
    with pytest.raises(JwtTokenSignatureMismatched):
        verify(f"{head}.{forged}.{sig}", SECRET)


def test_the_algorithm_is_pinned_so_alg_none_and_switching_fail() -> None:
    with pytest.raises(JwtTokenInvalid):
        verify(sign({"a": 1}, SECRET, "HS512"), SECRET, "HS256")
    with pytest.raises(JwtTokenInvalid):
        verify("eyJhbGciOiJub25lIn0.e30.", SECRET)


def test_unsupported_algorithms_are_not_implemented() -> None:
    with pytest.raises(JwtAlgorithmNotImplemented):
        sign({}, SECRET, cast(Alg, "RS256"))


def test_time_claims() -> None:
    with pytest.raises(JwtTokenExpired):
        verify(sign({"exp": future(-10)}, SECRET), SECRET)
    with pytest.raises(JwtTokenNotBefore):
        verify(sign({"nbf": future(60)}, SECRET), SECRET)
    with pytest.raises(JwtTokenIssuedAt):
        verify(sign({"iat": future(60)}, SECRET), SECRET)
    assert verify(sign({"exp": future(-10)}, SECRET), SECRET, exp=False)
    assert verify(sign({"nbf": future(60)}, SECRET), SECRET, nbf=False)
    assert verify(sign({"iat": future(60)}, SECRET), SECRET, iat=False)
    with pytest.raises(JwtTokenInvalid):
        verify(sign({"exp": "tomorrow"}, SECRET), SECRET)


def test_issuer_string_or_regex() -> None:
    token = sign({"iss": "https://auth.example"}, SECRET)
    assert verify(token, SECRET, iss="https://auth.example")
    assert verify(token, SECRET, iss=re.compile(r"auth\.example"))
    with pytest.raises(JwtTokenIssuer):
        verify(token, SECRET, iss="https://evil.example")
    with pytest.raises(JwtTokenIssuer):
        verify(sign({}, SECRET), SECRET, iss="x")


def test_audience_string_list_or_regex() -> None:
    one = sign({"aud": "api"}, SECRET)
    many = sign({"aud": ["web", "api"]}, SECRET)
    assert verify(one, SECRET, aud="api")
    assert verify(many, SECRET, aud=["mobile", "api"])
    assert verify(many, SECRET, aud=re.compile(r"^we"))
    with pytest.raises(JwtTokenAudience):
        verify(one, SECRET, aud="other")
    with pytest.raises(JwtPayloadRequiresAud):
        verify(sign({}, SECRET), SECRET, aud="api")


def app_with(middleware: Middleware, key: Key[dict[str, object]] = JWT_PAYLOAD) -> App:
    app = App()
    app.use("/api/*", middleware)

    @app.get("/api/me")
    def me(c: Context) -> Response:
        return c.json({"sub": c.get(key)["sub"]})

    return app


def test_middleware_accepts_a_bearer_token_and_exposes_the_payload() -> None:
    token = sign({"sub": "u1", "exp": future()}, SECRET)
    with app_with(jwt(secret=SECRET)).test() as client:
        resp = client.get("/api/me", headers={"authorization": f"Bearer {token}"})
    assert (resp.status, resp.json()) == (200, {"sub": "u1"})


def test_a_well_formed_token_signed_with_another_secret_is_rejected_at_the_signature() -> None:
    forged = sign({"sub": "admin", "exp": future()}, "not-the-secret")
    with app_with(jwt(secret=SECRET)).test() as client:
        resp = client.get("/api/me", headers={"authorization": f"Bearer {forged}"})
    assert (resp.status, resp.json()) == (401, {"error": "invalid_token"})


def test_middleware_rejects_missing_malformed_and_invalid_tokens() -> None:
    expired = sign({"sub": "u", "exp": future(-5)}, SECRET)
    with app_with(jwt(secret=SECRET)).test() as client:
        missing = client.get("/api/me")
        malformed = client.get("/api/me", headers={"authorization": "Bearer"})
        bad = client.get("/api/me", headers={"authorization": f"Bearer {expired}"})
        forged = client.get("/api/me", headers={"authorization": "Bearer a.b.c"})
    assert missing.status == malformed.status == bad.status == forged.status == 401
    assert missing.headers["www-authenticate"] == 'Bearer realm="/api/me"'
    assert 'error="invalid_token"' in bad.headers["www-authenticate"]
    assert bad.json() == {"error": "invalid_token"}


def test_middleware_reads_the_cookie_when_configured() -> None:
    token = sign({"sub": "c1"}, SECRET)
    with app_with(jwt(secret=SECRET, cookie="session")).test() as client:
        ok = client.get("/api/me", headers={"cookie": f"session={token}"})
        header_only = client.get("/api/me", headers={"authorization": f"Bearer {token}"})
    assert (ok.status, ok.json()) == (200, {"sub": "c1"})
    assert header_only.status == 401


def test_custom_header_name_key_alg_realm_and_verification_options() -> None:
    mine: Key[dict[str, object]] = Key("claims")
    token = sign({"sub": "k", "iss": "me"}, SECRET, "HS512")
    mw = jwt(secret=SECRET, alg="HS512", header_name="x-token", key=mine, realm="r", iss="me")
    with app_with(mw, mine).test() as client:
        assert client.get("/api/me", headers={"x-token": f"Bearer {token}"}).json() == {"sub": "k"}
        resp = client.get("/api/me")
    assert resp.headers["www-authenticate"] == 'Bearer realm="r"'
    with app_with(jwt(secret=SECRET, alg="HS512", iss="other")).test() as client:
        assert client.get("/api/me", headers={"authorization": f"Bearer {token}"}).status == 401


def test_a_token_with_junk_appended_to_the_signature_is_not_a_second_valid_spelling() -> None:
    token = sign({"sub": "a"}, "secret")
    for tampered in (token + "!!!!", token + "=", token + "\n"):
        with pytest.raises(JwtError):
            verify(tampered, "secret")


def test_absurdly_nested_json_is_an_invalid_token_not_a_crash() -> None:
    nested = base64.urlsafe_b64encode(b"[" * 100_000).decode().rstrip("=")
    with pytest.raises(JwtTokenInvalid):
        verify(f"{nested}.{nested}.AA", "secret")


def test_a_nan_expiry_is_refused() -> None:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(b'{"exp":NaN}').decode().rstrip("=")
    with pytest.raises(JwtTokenInvalid):
        decode(f"{header}.{body}.AA")
