# honoka-jwt

JSON Web Tokens, and a middleware that requires one. Standard library only.

```python
from honoka_jwt import JWT_PAYLOAD, jwt, sign, verify

app.use("/api/*", jwt(secret=SECRET, iss="my-service"))


def me(c):
    return c.json({"user": c.get(JWT_PAYLOAD)["sub"]})


token = sign({"sub": "42", "iss": "my-service", "exp": int(time.time()) + 3600}, SECRET)
claims = verify(token, SECRET, iss="my-service")
```

- **Algorithms: HS256, HS384, HS512 only.** RS*, ES*, PS* and EdDSA need a crypto library and
  are not implemented (`JwtAlgorithmNotImplemented`). `jwk` is not implemented either.
- `sign(payload, secret, alg="HS256")`, `verify(token, secret, alg, *, iss, aud, nbf, iat, exp)`
  and `decode(token)` (header and payload, **without** checking anything).
- `verify` pins the algorithm you pass, so `alg: none` and algorithm switching fail. The
  signature is checked before any claim. Then `exp`, `nbf` and `iat` are checked (turn each off
  with `exp=False` and so on), and `iss` and `aud` if given (a string, a regex, or for `aud` a
  list).
- Malformed tokens (bad base64url, junk after the signature, `NaN`, absurd nesting) raise
  `JwtTokenInvalid`, never an unrelated exception. All errors derive from `JwtError`.
- `jwt(secret=..., alg=..., cookie=None, header_name="authorization", realm=None, key=JWT_PAYLOAD,
  ...)` reads `Bearer <token>` from the header (any scheme is accepted, as in Hono), or from
  `cookie` if given. The verified payload is stored under `key`. A failure answers `401` with a
  `WWW-Authenticate: Bearer` challenge and a body that does not say why.
