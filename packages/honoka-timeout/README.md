# honoka-timeout

Answers `504` when the wrapped layers take longer than a limit.

```python
from honoka_timeout import timeout

app.use("*", timeout(10))                       # every route
app.get("/report", timeout(60), build_report)   # one route, a longer limit
```

- `timeout(seconds)`. The response is `{"error": "timeout"}` with status 504.
- An `async` handler is cancelled. A synchronous `def` handler runs in a thread and cannot be
  cancelled: the client still gets the 504, but the thread finishes its work.
