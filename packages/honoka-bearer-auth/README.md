# honoka-bearer-auth

`Authorization: Bearer <token>` against tokens you hold. For signed JWTs use
[`honoka-jwt`](../honoka-jwt).

```python
from honoka_bearer_auth import Reply, bearer_auth

app.use("/api/*", bearer_auth(token="a-long-random-token"))
app.use("/api/*", bearer_auth(token=["token-1", "token-2"]))

async def known(token: str, c) -> bool:
    return await api_keys.exists(token)

app.use("/v2/*", bearer_auth(verify_token=known, invalid_token=Reply(message={"error": "bad key"})))
```

- Give `token` (one or a list, compared in constant time) or `verify_token(token, c)` (sync or
  async); with neither it raises `ValueError`.
- Failures follow Hono: no header `401`, a malformed header `400`, a wrong token `401`, each with
  a `WWW-Authenticate: Bearer` challenge (`realm` and `prefix` are options).
- `no_authentication_header`, `invalid_authentication_header` and `invalid_token` take a `Reply`
  that replaces the challenge and/or the body of that one failure.
- `header_name` reads the token from another header.
