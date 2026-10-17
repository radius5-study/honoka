# honoka-basic-auth

HTTP Basic authentication.

```python
from honoka_basic_auth import USERNAME, BasicUser, basic_auth

app.use("/admin/*", basic_auth(username="admin", password="s3cret"))
app.use("/staff/*", basic_auth(users=[BasicUser("ann", "pw1"), BasicUser("bob", "pw2")]))


async def check(username: str, password: str, c) -> bool:
    return await users.verify(username, password)


app.use("/team/*", basic_auth(verify_user=check, realm="Team"))


def who(c):
    return c.text(c.get(USERNAME))
```

- Give one of `username`/`password`, `users`, or `verify_user(username, password, c)` (sync or
  async); with none of them it raises `ValueError`. Several can be combined.
- Comparison takes the same time whatever the input (both sides are hashed first, and every user
  is compared), so response time does not reveal which part was wrong.
- A failure answers `401` with `WWW-Authenticate: Basic realm="..."` (`realm` defaults to
  `Secure Area`). `invalid_user_message` replaces the JSON body: a string, a dict, or a function
  of the context.
- `USERNAME` is the typed key holding the user who got in; `on_auth_success(c, username)` runs
  after a successful login.
- Basic auth sends the password with every request. Use it only over HTTPS.
