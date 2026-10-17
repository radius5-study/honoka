# honoka-combine

Builds one middleware out of several.

```python
from honoka_combine import every, except_, some
from honoka_ip_restriction import ip_restriction
from honoka_bearer_auth import bearer_auth

app.use("/admin/*", some(ip_restriction(allow_list=["10.0.0.0/8"]), bearer_auth(token="t0ken")))
app.use("*", every(request_id(), logger()))
app.use("*", except_("/health", bearer_auth(token="t0ken")))
app.use("*", except_(["/public/*", lambda c: c.req.method == "OPTIONS"], bearer_auth(token="t0ken")))
```

- `some(*middlewares)` lets the request through when **any** of them passes (calls `next`). They
  run in order and the first pass wins. One that raises `HTTPError` or answers without calling
  `next` counts as failed; when all fail, the last failure is raised or returned. Limit: a
  passing middleware is cut off at its `next()`, so what it would do after the response comes
  back does not run.
- `every(*middlewares)` runs them in order; the first that raises or answers without calling
  `next` stops the chain.
- `except_(condition, middleware)` skips `middleware` when the request matches. `condition` is a
  path pattern (`/api/*` or an exact path), a function of the context, or a list of either.
  (`except` is a Python keyword.)
