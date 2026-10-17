# honoka-logger

An access log: one line per request, `METHOD path status duration`.

```python
import logging
from honoka_logger import logger

logging.basicConfig(level=logging.INFO)
app.use("*", logger())          # GET /users/1 200 1.4ms rid=3f9c...
```

- `logger(log=logging.getLogger("honoka.access"))` logs at `INFO` to that logger. Pass your own
  logger to send it elsewhere. Nothing is printed until logging is configured.
- When [`honoka-request-id`](../honoka-request-id) ran first, the line ends with `rid=<id>`.
- The path is escaped before it is logged, so control characters in a URL cannot forge log lines.
