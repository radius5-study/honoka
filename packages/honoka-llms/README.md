# honoka-llms

Serves your API's description as [`llms.txt`](https://llmstxt.org), for LLM agents.

```python
from honoka_llms import llms
from honoka_openapi import docs

docs(app, title="Taskboard API")
llms(app, title="Taskboard API", description="Projects and tasks for one team.")
```

- `/llms.txt`: a short index, one line per endpoint with a link into the OpenAPI document.
- `/llms-full.txt`: everything an agent needs to call each endpoint: parameters, request body
  fields with their constraints, and the responses.
- Both are generated from the route table on each request, so routes registered later appear too.
  Routes registered with `openapi=False` are left out.
- `llms(app, *, title, description=None, version="0.1.0", path="/llms.txt",
  full_path="/llms-full.txt", spec_path="/openapi.json")`. The index links to `spec_path`, so mount
  the document with [`honoka_openapi.docs`](../honoka-openapi) (or the same path yourself).
- The text comes from the same source as the OpenAPI document: handler docstrings (summary and
  description), return annotations, validators and `responses=` declarations.
