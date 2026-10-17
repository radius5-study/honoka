# honoka-serve-static

Serves files from a directory.

```python
from honoka_serve_static import serve_static

app.use("/static/*", serve_static(root="./public", rewrite_request_path=lambda p: p.removeprefix("/static")))
app.use("*", serve_static(path="index.html"))            # one file for every request
```

- `GET` and `HEAD` only. A miss, or any other method, goes on to the next handler, so routes
  and your 404 still answer.
- `root` is the directory. A path that leaves it (`../`, `%2e%2e`, absolute paths, a symlink
  pointing outside) is never served.
- `path` serves that one file whatever was requested. `rewrite_request_path` maps the request
  path first. A directory answers with its `index` file (default `index.html`).
- `mimes` maps an extension (`"txt"`) to a content type, ahead of the standard guess.
  `cache_control` is sent only when given.
- `precompressed=True` serves `file.br`, `file.zst` or `file.gz` when the client accepts that
  encoding and the file exists.
- `on_found(path, c)` and `on_not_found(path, c)` are hooks; the first may set headers with
  `c.header`.
