# honoka-timing

Adds a `Server-Timing` header with the time spent in the layers it wraps, so browser dev tools
show it.

```python
from honoka_timing import timing

app.use("*", timing())            # server-timing: app;dur=1.4
app.use("*", timing("db"))        # a second metric under another name
```

`timing(name="app")`: `name` is the metric name. Place it first to include everything after it.
