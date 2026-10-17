# honoka-trailing-slash

Redirects `GET /users/` to `/users` with `301`, keeping the query string.

```python
from honoka_trailing_slash import trim_trailing_slash

app.use("*", trim_trailing_slash())
```

- Only `GET` and `HEAD`, and only when `/users/` would have been a 404, so a route that really
  ends in a slash still works.
- The request path is canonical (`//evil.test/` is `/evil.test/`) and is percent-encoded into the
  `Location`, so a backslash or tab in the path (`/%5Cevil.test/`) becomes `/%5Cevil.test`, never an
  address a browser would read as another origin.
- Another way to treat both spellings alike is `App(strict=False)`.
