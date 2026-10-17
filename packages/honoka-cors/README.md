# honoka-cors

Cross-Origin Resource Sharing. Preflight requests are answered before routing.

```python
from honoka_cors import cors

app.use("*", cors(origins=["https://app.example"]))
app.use("/api/*", cors(origins=["*"], methods=["GET"], max_age=600))
```

- `origins` is required: a list of allowed origins, or `["*"]` for any.
- `methods` defaults to `GET, HEAD, PUT, POST, DELETE, PATCH` (`DEFAULT_METHODS`).
- `headers`: the allowed request headers. Left as `None`, the headers the browser asked for
  are echoed back.
- `expose` lists response headers scripts may read; `max_age` is how long a browser may cache
  the preflight.
- `credentials=True` allows cookies and `Authorization`. It cannot be combined with `"*"`
  (a `ValueError` at startup), because browsers refuse that pair.
- A request from an origin that is not listed passes through without CORS headers, so the browser
  blocks it. Responses for a listed origin carry `Vary: Origin`.
