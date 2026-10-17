# honoka-compress

Compresses response bodies with gzip or deflate, whichever the client prefers.

```python
from honoka_compress import compress

app.use("*", compress())
app.use("*", compress(encoding="gzip", threshold=2048))
```

- Picks from `Accept-Encoding`, honouring `q` values; gzip wins a tie. `encoding=` pins one.
- Only buffered bodies of at least `threshold` bytes (default 1024) whose content type is
  compressible. `content_type_filter` replaces the rule (`COMPRESSIBLE_CONTENT_TYPE_REGEX`, the
  same list as Hono) with a regex or a function.
- It drops `Content-Length`, adds `Vary: Accept-Encoding`, and turns a strong `ETag` weak, since
  the bytes changed.
- Skipped for `HEAD`, streams, files, `206` partial responses, a response that already has
  `Content-Encoding`, and `Cache-Control: no-transform`.
- Brotli and zstd are not implemented: they would need a dependency.
