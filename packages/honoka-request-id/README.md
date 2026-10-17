# honoka-request-id

Gives every request an id, makes it available to handlers, and echoes it in the response.

```python
from honoka_request_id import REQUEST_ID, request_id

app.use("*", request_id())

def whoami(c):
    return c.text(c.get(REQUEST_ID))
```

- `request_id(header="x-request-id")` is the middleware; `header` names the header read and written.
- A well-formed incoming id (`A-Za-z0-9._-`, up to 128 characters) is reused so a trace can span
  services. Anything else is replaced by a random one, so a client cannot put odd text in your logs.
- `REQUEST_ID` is the typed `Key[str]` to read it with `c.get(REQUEST_ID)`.
- Register it before [`honoka-logger`](../honoka-logger) so the access log can include the id.
