# honoka

A Hono-style web framework for Python 3.13+: a thin core centred on `Context`, one
package per middleware, typed end to end. Runs on any ASGI server (Granian, uvicorn) and
Cloudflare Python Workers (ASGI). The design is in [rfc/honoka-framework-rfc.md](rfc/honoka-framework-rfc.md).

```python
import msgspec
from honoka import App, Context, Json
from honoka_msgspec import MsgspecCodec, validator

app = App(json_codec=MsgspecCodec())


class NewUser(msgspec.Struct):
    name: str


@app.get("/users/{id:int}")
async def get_user(c: Context) -> Json[dict[str, object]]:
    return c.json({"id": int(c.req.param("id"))})


@app.post("/users", validator(json=NewUser))
async def create_user(c: Context) -> Json[NewUser]:
    return c.json(c.req.valid(NewUser))
```

```sh
granian --interface asgi app:app      # or uvicorn; on Workers: App(threads=False), asgi.entrypoint(app)
honoka routes app                     # list routes
honoka check app                      # find handlers that read data no validator provides
```

Test without a server: `with app.test() as client: client.get("/users/1")`
(it drives the ASGI entry).

## Packages

`honoka` (core, no dependencies), `honoka-msgspec`, `honoka-openapi`, `honoka-llms`,
`honoka-cli`, and middleware: `-request-id`, `-logger`, `-timing`, `-secure-headers`,
`-cors`, `-csrf`, `-body-limit`, `-timeout`, `-trailing-slash`.

## Development

```sh
uv sync --all-packages
uv run pytest && uv run ruff check . && uv run ty check && uv run pyrefly check
```

Benchmarks: [benchmarks/README.md](benchmarks/README.md).
