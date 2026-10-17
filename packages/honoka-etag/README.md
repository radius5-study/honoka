# honoka-etag

Adds an `ETag` to responses and answers `304 Not Modified` when the client already has them.

```python
from honoka_etag import etag

app.use("*", etag())
app.use("/api/*", etag(weak=True))
```

- Applies to buffered `200` responses to `GET` and `HEAD`. Streams, files and empty bodies are
  left alone.
- The tag is a SHA-1 of the body (`generate_digest=` takes another function from bytes to
  bytes); `weak=True` makes it `W/"..."`.
- An `ETag` the handler already set is kept and used for the comparison, not recomputed.
- When `If-None-Match` matches, the answer is `304` carrying only `retained_headers` (by default
  `cache-control`, `content-location`, `date`, `etag`, `expires`, `vary`).
- Place it inside [`honoka-compress`](../honoka-compress), so the tag is computed on the bytes
  before compression.
