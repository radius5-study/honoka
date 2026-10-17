# honoka-method-override

Lets an HTML form (which can only send GET and POST) reach `PUT`, `PATCH` and `DELETE` routes.

```python
from honoka_method_override import method_override

asgi_app = method_override(app)                       # <input type="hidden" name="_method" value="DELETE">
asgi_app = method_override(app, header="X-HTTP-Method-Override")
asgi_app = method_override(app, query="_method", form="m", max_body=16 * 1024)
```

- **It wraps the app instead of using `app.use`.** honoka routes on the ASGI scope's method
  before any middleware runs, so the method has to change ahead of the app. Serve the wrapper
  (`granian ... module:asgi_app`); in tests use `honoka.testing.asgi_call(asgi_app, ...)`,
  since `app.test()` takes only an `App`.
- Only `POST` is touched. The value comes from the `header` if that option is set, else the
  `query` parameter if set, else the urlencoded body field `form` (default `_method`).
- Only `PUT`, `PATCH` and `DELETE` are accepted. A cross-site form saying `_method=GET` must not
  become a "safe" request that slips past a CSRF check that trusts the method.
- The form is read only up to `max_body` bytes (default 64 KiB). What was read is replayed to
  the app followed by the rest of the stream, so handlers and
  [`body_limit`](../honoka-body-limit) still see the whole body.
