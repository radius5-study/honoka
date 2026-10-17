# honoka

The core of honoka: a Hono-style web framework for Python 3.13+ on ASGI. It has no dependencies.
Everything else (validation, auth, CORS, ...) is a separate package that plugs in as middleware;
see the [repository README](../../README.md) for the list.

```python
from honoka import App, Context

app = App()


async def get_user(c: Context):
    return c.json({"id": c.req.param("id", int)})


app.get("/", lambda c: c.text("Hello honoka!"))
app.get("/users/{id:int}", get_user)
```

```sh
granian --interface asgi app:app        # or uvicorn app:app
```

Routes are registered by calling `app.get(path, *middleware, handler)`: the last argument is the
handler, the ones before it are middleware. There are no route decorators. The same call exists
for `post`, `put`, `patch`, `delete`, `head`, `options`, `query` (the HTTP `QUERY` method), `all`
and `on(methods, paths, ...)`; `ws(path, handler)` registers a WebSocket.

A handler is a function of one `Context` that returns a response, `async def` or plain `def`. A
plain `def` handler runs in a worker thread so it cannot block the event loop; pass `inline=True`
to run it on the loop. Extra keyword options (`status=201`, `responses=...`) are stored with the
route for plugins such as `honoka-openapi` (`app.route_table()`); the core never reads them.

## Routing

| Pattern | Matches |
|---|---|
| `/users` | exactly that path |
| `/users/{id}` | one non-empty segment, as text |
| `/users/{id:int}` | an integer (also `float`, `uuid`, `slug`); anything else is a 404 |
| `/files/{rest:path}` | the rest of the path, last segment only |
| `/img/{name:re:[a-z]+\.png}` | a regular expression (no capture groups; may span `/`) |
| `/posts/{year}/{slug}?` | the last segment is optional |
| `/static/*`, `/a/*/c`, `/v*` | the prefix and everything below it; one segment; a segment that starts with `v` |

When several match, the most specific wins: literal, then fixed converters, then `re:`, then
`{name}`, then `path`, then `*`. `App(strict=False)` treats `/a` and `/a/` alike.
`App(router=...)` picks the matching strategy (`SmartRouter` by default, `RegExpRouter`,
`TrieRouter`). A bad pattern raises `ValueError` when the route is registered.

## Context

`c` carries the request and builds the response.

```python
async def handler(c: Context):
    c.header("x-trace", "1")  # set (or remove with None, append=True to add)
    c.cookie("seen", "1", max_age=3600)  # HttpOnly and SameSite=Lax by default
    return c.json({"ok": True}, 201)  # also text(), html(), body(), redirect()
```

- Responses: `json`, `text`, `html`, `body`, `redirect`, `file` (a path, optionally below a `root`
  so `../` answers 404), `stream`, `stream_text`, `sse`, `not_found()`; `c.status(code)` sets the
  default status. `headers=` is accepted by `json`, `text`, `html` and `body`.
- `c.set(KEY, value)` / `c.get(KEY)` store values under typed keys:
  `USER = Key[User]("user")`. A wrong type is a type error, a missing key a `LookupError`.
  `app.provide(KEY, value)` publishes an app-wide value that `c.get(KEY)` also finds.
- Cookies: `c.cookie`, `c.signed_cookie` (HMAC, bound to the cookie's name), `c.delete_cookie`,
  with `expires`, `priority`, `partitioned` and the `__Secure-` / `__Host-` prefixes.
- Server-Sent Events: `c.sse(events)` takes an async iterator of `str` or `SSEEvent`.

## Request

`c.req` exposes `method`, `path`, `url`, `query_string`, `client`, and:

- `param(name, int)` a path parameter, converted; a value that does not convert is a 400
- `query(name)` and `queries(name)`; `header(name)`, `cookie(name)`, `signed_cookie(name, secret)`.
  Called without a name, `query()`, `header()` and `cookie()` return every value as a dict.
- `await body()`, `await text()`, `await json()` (typed `object`, so you must narrow it),
  `await parse_body()` for urlencoded and multipart forms (`UploadedFile` for files)
- `valid(T)` the object a validator middleware produced; `scope` the raw ASGI scope

## Middleware

```python
async def timing(c: Context, call_next: Next) -> Response:
    response = await call_next()
    response.headers.append(("server-timing", "app;dur=1"))
    return response


app.use("*", timing)  # every path
app.use("/api/*", auth)  # a prefix and below, including routes registered later
app.get("/admin", auth, admin)  # one route
```

Middleware is `async def (c, call_next)`; it answers by returning a response without calling
`call_next`. The first registered is the outermost. A middleware is attached by the **request
path**, not by the text of a route pattern, so `use("/admin/*")` covers `/{section}/panel` when the
path is `/admin/panel`. It also runs for WebSockets and mounted apps (a rejected WebSocket closes
with 1008) and for unmatched requests.

## Errors

Raise `HTTPError(status, code, detail=None)` anywhere; the answer is `{"error": code}` (plus
`detail`). Anything else becomes a logged `500 {"error": "internal_error"}`.

```python
async def fail(c: Context) -> Response: ...  # the error is c.error


app.on_error(fail)  # a handler of one parameter answers
app.not_found(lambda c: c.json({"error": "no such page"}, 404))
```

`on_error` and `not_found` are routable, stacking steps as in Hono v5: give a path first, and a
step `(c, next)` may hand over to the next.

## Structure

- `app.route("/users", users_app)` mounts a sub-app: its routes, middleware and fallbacks come
  with the prefix.
- `app.base_path("/api")` returns a view of the same app whose registrations all sit below the
  prefix.
- `app.mount("/legacy", asgi_app)` serves any other ASGI app below a prefix (it answers before
  this app's routes, and sees the path without the prefix).
- `App(get_path=fn)` routes on something other than the path, such as the host.
- `app.on_startup(fn)` / `app.on_shutdown(fn)` hooks run with the server's lifespan, for opening and
  closing pools. `await app.start()` / `await app.stop()` do the same by hand.

## WebSockets

```python
async def chat(ws: WebSocket) -> None:
    await ws.accept()
    async for message in ws:
        await ws.send_text(message)


app.ws("/chat/{room}", chat)
```

## Testing

`app.test()` drives the ASGI entry without a server and runs the lifespan:

```python
with app.test() as client:
    resp = client.get("/users/1")
    assert (resp.status, resp.json()) == (200, {"id": 1})
    client.post("/users", json={"name": "ann"})  # also form=, content=, headers=
    with client.websocket("/chat/a") as ws:
        ws.send_text("hi")
```

For something that is not an `App` (an ASGI wrapper), use `honoka.testing.asgi_call`.

## Not here

Validation (`honoka-msgspec`), JWT, CORS, sessions-style helpers and the rest are packages. Host
specifics, such as Cloudflare Workers' `env`, belong in separate adapter packages, never in the
core. Hono's chained `app.get().post()`, `c.res`, `c.render` and `executionCtx` are not carried
over.
