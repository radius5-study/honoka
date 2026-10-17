# honoka-trailing-slash

Redirects `GET /users/` to `/users` with `301`, keeping the query string.

```python
from honoka_trailing_slash import trim_trailing_slash

app.use("*", trim_trailing_slash())
```

- Only `GET` and `HEAD`, and only when `/users/` would have been a 404, so a route that really
  ends in a slash still works.
- Leading slashes are collapsed first: `//evil.test/` redirects to `/evil.test`, never to a
  protocol-relative URL.
- Another way to treat both spellings alike is `App(strict=False)`.
