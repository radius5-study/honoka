# honoka

A Hono-style web framework for Python 3.13+: a thin core centred on `Context`, one
package per middleware, typed end to end. Runs on any ASGI server (Granian, uvicorn) and
Cloudflare Python Workers (through a separate adapter package). The design is in [rfc/honoka-framework-rfc.md](rfc/honoka-framework-rfc.md).

```python
import msgspec
from honoka import App, Context, Json
from honoka_msgspec import MsgspecCodec, validator

app = App(json_codec=MsgspecCodec())


class NewUser(msgspec.Struct):
    name: str


async def get_user(c: Context) -> Json[dict[str, object]]:
    return c.json({"id": int(c.req.param("id"))})


async def create_user(c: Context) -> Json[NewUser]:
    return c.json(c.req.valid(NewUser))


app.get("/health", lambda c: c.text("ok")).get("/users/{id:int}", get_user).post(
    "/users", validator(json=NewUser), create_user
)
```

Registration is a call: `app.get(path, *middleware, handler)` returns the app, so calls chain as in
Hono. A `lambda` is fine for one expression; anything with `await` is a `def` passed by name.
The decorator form (`@app.get("/users")` on a `def`) works too.

```sh
granian --interface asgi app:app      # or uvicorn; host adapters such as Cloudflare Workers are separate packages
honoka routes app                     # list routes
honoka check app                      # find handlers that read data no validator provides
```

Test without a server: `with app.test() as client: client.get("/users/1")`
(it drives the ASGI entry).

## Install

Not on PyPI yet. Install from GitHub with uv; every package lives in `packages/<name>` of this repo,
so name each one with its `subdirectory`:

```toml
[project]
dependencies = ["honoka", "honoka-msgspec", "honoka-cors"]

[tool.uv.sources]
honoka = { git = "https://github.com/radius5-study/honoka", subdirectory = "packages/honoka" }
honoka-msgspec = { git = "https://github.com/radius5-study/honoka", subdirectory = "packages/honoka-msgspec" }
honoka-cors = { git = "https://github.com/radius5-study/honoka", subdirectory = "packages/honoka-cors" }
```

or one at a time: `uv add "honoka @ git+https://github.com/radius5-study/honoka#subdirectory=packages/honoka"`.
List `honoka` itself as a dependency too: the other packages depend on it by name, and uv only
reads `tool.uv.sources` for the packages your own project names.

## Packages

`honoka` (core, no dependencies), `honoka-msgspec`, `honoka-openapi`, `honoka-llms`,
`honoka-cli`, and middleware, one package each:

- observability and plumbing: `-request-id`, `-logger`, `-timing`, `-timeout`, `-body-limit`,
  `-trailing-slash`, `-powered-by`, `-pretty-json`, `-method-override`, `-language`
- security: `-secure-headers`, `-cors`, `-csrf`, `-basic-auth`, `-bearer-auth`, `-jwt`,
  `-ip-restriction`
- content: `-etag`, `-compress`, `-serve-static`
- composition: `-combine` (`some`, `every`, `except_`)

## Development

```sh
uv sync --all-packages
uv run pytest && uv run ruff check . && uv run ty check && uv run pyrefly check
```

Benchmarks: [benchmarks/README.md](benchmarks/README.md).
