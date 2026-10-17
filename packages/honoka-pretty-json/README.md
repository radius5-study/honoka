# honoka-pretty-json

Indents JSON responses when the client asks for it, so `curl` output is readable.

```python
from honoka_pretty_json import pretty_json

app.use("*", pretty_json())  # GET /users?pretty
```

- `pretty_json(space=2, query="pretty", force=False)`. The flag counts with any value, even
  empty. `force=True` indents always, which suits a development server.
- It re-indents what the app's own JSON codec produced, so msgspec Structs, datetimes and
  anything else the codec encodes keep working. Responses that are not JSON are untouched.
