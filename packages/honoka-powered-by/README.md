# honoka-powered-by

Adds `X-Powered-By: honoka` to every response.

```python
from honoka_powered_by import powered_by

app.use("*", powered_by())
app.use("*", powered_by("my service"))
```

`powered_by(server_name="honoka")`. Many hardening guides advise leaving this header out; it is
here for parity with Hono.
