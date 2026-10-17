# honoka-csrf

Rejects cross-origin form submissions with `403`.

```python
from honoka_csrf import csrf

app.use("*", csrf())
app.use("*", csrf(origins=["https://admin.example"]))
```

- Checked: unsafe methods (not `GET`/`HEAD`/`OPTIONS`) whose content type is a form
  (`application/x-www-form-urlencoded`, `multipart/form-data`, `text/plain`) or no content type at
  all (read as `text/plain`, as in Hono), the requests a browser sends without a preflight. JSON APIs are not affected. This matches Hono.
- Allowed: the same origin (by `Origin`, `Sec-Fetch-Site` or `Host`), and the extra `origins`.
- The answer is `{"error": "csrf_rejected"}`.
- A non-browser client that sends no `Origin` and no content type (a bare `curl -X DELETE`) is
  refused like any other cross-site request; give it a content type such as `application/json`, or
  leave the route out with `except_`.
