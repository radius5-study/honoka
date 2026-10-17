# honoka-msgspec

[msgspec](https://jcristharif.com/msgspec/) for honoka: fast JSON, and request validation against
`msgspec.Struct` types.

```python
import msgspec
from honoka import App, Context, Json
from honoka_msgspec import MsgspecCodec, validator

app = App(json_codec=MsgspecCodec())          # c.json() now encodes Structs directly


class NewUser(msgspec.Struct):
    name: str
    age: int = 0


class Page(msgspec.Struct):
    limit: int = 20
    offset: int = 0


async def create_user(c: Context) -> Json[NewUser]:
    body = c.req.valid(NewUser)               # already validated and typed
    return c.json(body, 201)


app.post("/users", validator(json=NewUser, query=Page), create_user)
```

- `MsgspecCodec()` goes in `App(json_codec=...)`. Responses and `c.req.json()` use msgspec.
- `validator(json=, query=, param=, header=, cookie=, form=)` validates the request parts it is
  given, in that order, and stores the results for `c.req.valid(T)`. `json` takes any type msgspec
  can decode (a Struct, `list[Item]`, ...); the others take a `Struct` whose fields name the keys.
  Query, path, header, cookie and form values are strings, so they are converted
  (`"3"` to `3`); a field typed as a list takes every value of a repeated key.
- A failure answers `400` with every detail a client needs:

  ```json
  {"error": "validation_failed",
   "fields": [{"in": "json", "path": "age", "expected": "int", "received": "ten"}],
   "schema": "/openapi.json#/components/schemas/NewUser"}
  ```

  The first failing part stops validation (msgspec reports one error at a time). A body that is
  not JSON at all is `400 invalid_json`; a form with the wrong content type is `415`.
- `c.req.valid(T)` returns the type `T` with nothing passing through `Any`; pass `target=` when
  two parts share a type.
- [`honoka-openapi`](../honoka-openapi) reads these validators to document the route, and
  `honoka check` reads them to find a `c.req.valid(T)` that no validator provides.
