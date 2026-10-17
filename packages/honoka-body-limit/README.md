# honoka-body-limit

Refuses request bodies over a size with `413`.

```python
from honoka_body_limit import body_limit

app.use("*", body_limit(1024 * 1024))
```

- `body_limit(max_bytes)`. The body is `{"error": "payload_too_large"}`.
- A `Content-Length` over the limit is refused before anything is read. A request without one,
  or with a false one, is counted as the body is read, so chunked uploads are covered too.
