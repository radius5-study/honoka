# RFC: honoka — A Thin, Strictly Typed Python Web Framework for ASGI

- Status: Draft
- Created: 2026-10-16
- Intended readers: implementing agents / co-developers

---

## 1. Overview

We will build a web framework that brings the philosophy of Hono (JS) — "a thin core, Context-centric, middleware-oriented, and not forcing unnecessary things on the user" — to Python. honoka speaks one server interface, ASGI, so the same app runs on Granian, uvicorn and Cloudflare Python Workers (as Hono runs on any runtime that speaks the Web standard). Granian is the recommended server: it keeps I/O in Rust, so honoka itself stays pure Python for v0/v1 and minimizes per-request framework overhead through registration-time precompilation and strict typing. Routing moves to Rust only if benchmarks justify it (see 12.2). At the same time, we adopt an "agent-first" stance: the goal is a framework that is **easy for AI agents to write**, and that produces APIs that are **easy for agents to use as clients**.

## 2. Background and Motivation

### 2.1 Problems with existing frameworks

- **FastAPI**: The de facto standard, but its RPS is low (the combined cost of decorator introspection, Pydantic, and DI); it has no concept of a request context (the context is scattered across function-argument DI); and type expressions such as `Annotated[Query, Depends(...)]` are poor
- **Swapping the server (uvicorn → Granian) does not solve it by itself**: Synthetic benchmarks show multi-fold differences, but in real applications (DB-bound) the difference is within 5–10%. That also bounds what any framework swap can gain, so honoka does not sell raw speed alone: the goal is to keep framework overhead as close as possible to a bare ASGI app, and to prove it with benchmarks against other frameworks (see 11.1)
- **Hono v5**: Raising type safety (e.g., `c.req.json()` now returning `unknown`) has come at the expense of writing ergonomics. There is room on the Python side to achieve equivalent safety with better DX

### 2.2 Technical foundations

- **ASGI**: The community-driven de facto specification between server and application, and the one that Cloudflare Python Workers, uvicorn, Granian and Hypercorn all serve. It carries structural overhead from round-tripping event dicts
- **Granian**: A server written in Rust (tokio + hyper + PyO3). Supports ASGI/WSGI/RSGI, HTTP/1 and HTTP/2, and WebSocket, and has a production track record. Not supported: HTTP/3, trio/gevent, and some ASGI extensions

### 2.3 Decision: ASGI only, no RSGI

Granian's own interface, RSGI, was the first target. It is dropped because it buys little and costs a second path:

- **Portability is a requirement**: Workers (Pyodide) and every other host speak ASGI only. An RSGI path would be a second entry to build, test and keep identical
- **Measured gap** (one Granian worker, `ab`, honoka's own app, three alternating runs on a laptop, noisy): ASGI was 5–15% slower than RSGI while honoka converted the ASGI scope into an RSGI-shaped one. Once the core read the ASGI scope directly, the same test gave about 114k rps twice (and one noisy 56k), level with RSGI's best run, so the cost was the conversion, not ASGI. For a bare hello-world app, raw ASGI and raw RSGI measured the same (about 43–45k rps per worker). In DB-bound services the framework is a small share of the request, so we accept the gap
- **Revisit if**: a benchmark shows the ASGI scope/send round trip dominating real workloads. The fix would be a thinner Context over the ASGI scope, not a second interface

## 3. Design Principles

1. **Context first**: Handlers take only a `Context` argument. This eliminates DI hell
2. **Touch the scope only when asked**: The ASGI scope is already a dict, so nothing is converted up front. Headers are indexed, the query parsed and the body read on first access, and per-request state (cookies, stored values) is created only when used
3. **Synchronous handlers are supported, and safe by default**: `def` handlers run in a worker thread so that blocking I/O cannot stall the event loop. Calling them inline on the loop is an explicit opt-in (`inline=True`); hosts without threads are an adapter concern (4.3)
4. **Keep the core thin**: The core contains no schema validation, OpenAPI, or authentication. These are middleware/plugins; each official middleware is its own package, e.g. `honoka-cors` (see 9.4). The core has no third-party dependencies: JSON goes through a pluggable codec (`App(json_codec=...)`), which is the standard library by default and `MsgspecCodec` from honoka-msgspec for Structs
5. **Validation is a single layer**: msgspec only. Do not split it across multiple layers (e.g., Rust-side validation), which would multiply the owners of "correctness". The core only converts scalar path/query parameters (`c.req.param(name, type)`); that is parsing, not schema validation
6. **Standard type features only**: Do not rely on mypy plugins. Passing under the strictest settings of ty and Pyrefly is a design requirement
7. **Agent-first**: No implicit context, startup-time checks for everything introspectable (duplicate routes, unused path parameters, missing type hints), `honoka check` in CI for the rest, machine-readable errors, and llms.txt
8. **Types ride on values, not on `Context`**: `Context` has no type parameters. Types flow through typed keys (`Key[T]`), type arguments (`c.req.valid(T)`), and return types (`Json[T]`), so that every read and write is checked and the checkers need only simple generics

## 4. Architecture

```
Client
   ↕ HTTP/1 and HTTP/2, WebSocket
Granian (Rust: tokio + hyper) / uvicorn / Cloudflare Python Workers
   ↕ ASGI
honoka core
   ├─ Routing (RegExpRouter style in pure Python: one compiled regex per method; Rust only if benchmarks justify it, see 12.2)
   ├─ Context (state created on first use)
   └─ Middleware onion execution
   ↕
honoka-msgspec / honoka-openapi / other plugins
```

### 4.1 The ASGI entry

```python
async def __call__(self, scope, receive, send):
    if scope["type"] == "http":
        ctx = Context(Request(scope, receive))  # nothing is parsed yet
        handler = router.match(scope["method"], scope["path"])
        resp = await dispatch(ctx, handler, middlewares)
        await emit(resp, send)  # one response start and one body; a Stream sends chunks as produced
    elif scope["type"] == "lifespan":
        ...  # startup / shutdown hooks
```

The app object is the ASGI callable. There is no adapter layer and no second interface; the test client drives this same entry (`app.test()`).

### 4.2 Responsibility boundaries if a Rust core is adopted (conditional, see 12.2)

- **Does**: routing, holding the Context object, emitting responses (the HTTP layer belongs to the server)
- **Does not**: validation (does not look inside the JSON), business logic

### 4.3 Hosts and adapters

The core knows ASGI and nothing about any host. Anything that depends on where the app runs lives in a separate adapter package, one per host (e.g. `honoka-workers`).

- **Any ASGI server** (Granian, uvicorn): `granian --interface asgi app:app`. Lifespan is handled: startup and shutdown hooks run when the server announces them
- **Adapters own host specifics**: Cloudflare Python Workers has no lifespan, no threads (so `def` handlers must run on the loop), and passes its bindings in `scope["env"]` (documented for WSGI; to verify for ASGI) and `workers.env`. An adapter wraps the app (`workers.asgi.entrypoint`), starts it, and exposes `c.env` through a typed `Key`. None of this is in the core
- **Seams the core must offer** so adapters need no patches: read access to the raw ASGI scope, a way to start and stop the app (`app.start()` / `app.stop()`, present), and a way to run sync handlers on the loop (per route `inline=True`, present; an app-wide switch is the adapter's design problem). Decide when the first adapter is written
- **WebSocket** scopes are closed with 1000 until v3
- **Open**: `c.file` reads the whole file into memory. Servers that offer the `http.response.pathsend` extension (Granian does, to be verified) could send the file themselves
- **Known limits**: `root_path` is ignored, so mounting under a sub-path is a v2 `mount` concern; a client disconnect while the body is being read ends the read with what arrived

## 5. Core API

### 5.1 Routing and handlers

```python
from honoka import App, Context, HTTPError, Json, Response

app = App()


# Synchronous handler (runs in a worker thread; use inline=True to call it on the loop)
@app.get("/health")
def health(c: Context) -> Response:
    return c.text("ok")


# Asynchronous handler. The return type is the response body type (used by OpenAPI)
@app.get("/users/{user_id}")
async def get_user(c: Context) -> Json[User]:
    user = await c.get(DB).fetch(c.req.param("user_id", int))  # param() parses and converts
    if user is None:
        raise HTTPError(404, "user_not_found")
    return c.json(user)  # c.json[T](obj: T) -> Json[T]
```

- Decorators return the function **as is, without wrapping it** (so that types are not broken)
- The handler signature is unified as `(c: Context) -> Response`; handlers that return a typed body annotate `Json[T]` (a subtype of `Response`)
- `c.req.param(name, type)` raises a structured 400 when the conversion fails
- Route groups: `app.route("/users", users_app)` mounts a sub-app under a prefix (Hono's `app.route`)
- Lifecycle: `@app.on_startup` / `@app.on_shutdown` hooks create and close app-wide resources (e.g., DB pools), which are published with `app.provide(KEY, value)`
- Methods: `get`, `post`, `put`, `patch`, `delete`, `head`, `options`, `query` (the HTTP `QUERY` method); `app.on(["GET", "POST"], ["/a", "/ja/a"])` registers one handler for several methods and paths (a method may be any token, e.g. `"PURGE"`) and `app.all(path)` for every common one. Hono's chained form (`app.get(...).post(...)`) is not carried over: decorators return the handler, and `app.on` covers the case
- Route patterns: a segment is a literal, `{name}` (any non-empty text), `{name:int|float|uuid|slug}`, `{rest:path}` (the rest of the path, last segment only), `{name:re:REGEX}` (Hono's `:name{regex}`; no capturing groups, must not match the empty string, and it may span `/` as in `{file:re:.+\.png}`), a lone `*` (any one non-empty segment, or the prefix and everything below it when last), or `prefix*` (a segment starting with `prefix`, possibly nothing more). A `*` elsewhere inside a segment is an ordinary character (Hono v5). A trailing `}?` makes the last segment optional (`/api/animal/{type}?` is `/api/animal` and `/api/animal/{type}`; `c.req.params` lacks `type` when it is left out). A value a converter rejects is a 404, not a 400. Priority at one position: literal, then fixed converters (`int`, `float`, `uuid`, `slug`), then `re:`, then `{name}`, then `path`, then `*`. A bad pattern raises `ValueError` when the route is declared
- Groups: `app.route(prefix, sub)` mounts a sub-app; `app.base_path("/api")` returns a view of the same app whose routes, middleware and fallbacks sit below the prefix (Hono's `basePath`); `app.mount(prefix, asgi_app)` serves any other ASGI app below a prefix, answering before this app's routes and middleware
- Routing on something other than the path: `App(get_path=fn)` (Hono's `getPath`, e.g. to route on the host)
- WebSocket: `@app.ws("/chat/{room}")` registers `async def handler(ws: WebSocket)`; the handler calls `accept()`, then `receive_text/bytes/json`, `send_text/bytes/json`, `close()`, or iterates `async for message in ws`. Middleware does not run for WebSocket routes; a path with no handler is refused. `client.websocket(path)` drives it in tests
- Trailing slash: strict by default (`/a/` is not `/a`); `App(strict=False)` treats them as one path, as in Hono
- Router: paths without parameters are a dict lookup; the others are folded into a trie and compiled into one regular expression per method (Hono's RegExpRouter). Like Hono's SmartRouter, the first request tries the RegExpRouter and falls back to walking the trie only if building the expression raises `UnsupportedPathError`; with neither usable it fails loudly. `App(router=...)` takes any router factory. The fallback is a safety net, never a speed choice, and both paths share one test suite
- Route metadata: decorators accept extra keyword options (e.g., `responses={...}`, `tool=True`) and store them in `app.route_table()`. The core never interprets them; plugins do

### 5.2 Context

```python
class Key[T]:
    def __init__(self, name: str) -> None: ...


class Context:  # no type parameters
    req: Request  # access to header/query/param/body (read on first use)

    def set[T](self, key: Key[T], value: T) -> None: ...
    def get[T](self, key: Key[T]) -> T: ...  # raises LookupError if the key was never set
    def json[T](
        self, obj: T, status: int | None = None, headers: Mapping[str, str] | None = None
    ) -> Json[T]: ...
    def text(self, body: str, status: int | None = None, headers: ... = None) -> Response: ...
    def html(self, body: str, status: int | None = None, headers: ... = None) -> Response: ...
    def body(
        self, data: str | bytes, status: int | None = None, headers: ... = None
    ) -> Response: ...
    def status(self, code: int) -> None: ...  # the default status of the response built next
    def header(self, name: str, value: str | None, *, append: bool = False) -> None: ...
    def not_found(self) -> NoReturn: ...  # answered by the not_found steps (5.4)

    error: Exception | None  # the error being handled, inside on_error
    route: str | None  # the matched route template
```

```python
# keys.py: typed keys. Both set and get are type-checked
USER: Key[User] = Key("user")
DB: Key[Pool] = Key("db")  # app-wide values use the same mechanism (see app.provide)
```

- Values passed between middleware and handlers (and app-wide resources such as DB pools) are typed through `Key[T]`. A value of the wrong type in `c.set(USER, 1)` is a type error, and keys are found by importing them, so they can be grepped (no implicit context)
- Validated inputs are read by type: `c.req.valid(CreateUser)` returns `CreateUser`. Asking for a type that the route did not validate is reported by `honoka check` (CI) and, as a fallback, raises on the first request
- Because `Context` has no type parameters, `Context[...]` annotations, the `Env` TypedDict, and the decorator/annotation double declaration are not needed

### 5.3 Middleware

Hono's onion model:

```python
async def auth(c: Context, call_next: Next) -> Response:
    token = c.req.header("authorization")
    c.set(USER, await verify(token))
    return await call_next()


app.use("/api/*", logger())
app.use("/admin/*", auth)
```

- The next-function is named `call_next` (not `next`) so that it does not shadow the builtin (ruff `A002`)
- Middleware is async. Handlers can also be attached per route: `@app.get("/users/{user_id}", auth)`

- Path matching is completed in the core (pure Python in v0/v1; it may move to Rust, see 12.2), and the middleware chain of each route is composed once at registration

### 5.4 Error handling

```python
@app.on_error  # a final handler: it answers
def on_error(c: Context) -> Response: ...  # the error is read from c.error


@app.on_error  # or a step: answer, or hand over with next()
async def only_runtime(c: Context, call_next: Next) -> Response: ...


app.not_found("/api/*", mark, answer)  # path first, then any number of steps
```

- Handlers signal errors by raising `HTTPError(status, code, detail=None)`, which the core renders in the machine-readable format below (`code` becomes the `error` field). `ValidationFailed` is a subclass of `HTTPError`, defined in the core and raised by honoka-msgspec
- `on_error` and `not_found` follow Hono v5, where they register routable middleware instead of one replaceable handler:
  - A step is `(c, next)` middleware or a `(c)` final handler (told apart by its required parameters). The error is read from `c.error`
  - Registrations **stack**. A step returns a response to stop, or calls `next()` to delegate; when the steps run out, the built-in answer is used (`default_error_response`, the plain 404)
  - An optional path comes first (`app.not_found("/api/*", ...)`). It is independent of the HTTP method; without one it is `*`
  - `app.route(prefix, sub)` imports the sub-app's registrations below the prefix, deeper sub-apps first, then registration order
  - `not_found` runs for unmatched requests and for `c.not_found()`; an `HTTPError(404, ...)` raised by a handler is an ordinary error and goes to `on_error`
  - An exception raised inside a step goes to the built-in answer and never escapes the app
- Not carried over from Hono v5: thrown non-Error values (Python raises only exceptions), `getColorEnabledAsync`, `app.fire`, `mount` (v2, 9.4). `c.json` on a value the codec cannot encode raises `TypeError`, as v5 does
- `c.req.query()` and `c.req.queries()` without a name return every parameter (first value, and all values)
- Wildcards follow v5: a lone `*` in the middle is one non-empty segment, `x*` at the end of a segment may match nothing, and a `*` in the middle of a segment is a literal

- Validation errors are short-circuited by the middleware with a structured 400 (before the handler runs)
- All error responses use a machine-readable format (see below)

## 6. Validation (honoka-msgspec)

**Not included in the core. Provided as an official middleware package.**

```python
from honoka_msgspec import validator
import msgspec
from typing import Annotated


class CreateUser(msgspec.Struct):
    name: Annotated[str, msgspec.Meta(min_length=1, max_length=100)]
    age: Annotated[int, msgspec.Meta(ge=0)]


@app.post("/users", validator(json=CreateUser, query=SearchQuery))
async def create_user(c: Context) -> Json[User]:
    body = c.req.valid(CreateUser)  # CreateUser, already validated. Does not pass through unknown
    q = c.req.valid(SearchQuery)  # SearchQuery
    ...
```

Reading an unvalidated body is explicit and typed `object`: `await c.req.json()` (never `Any`).

Implementation approach:

- `validator()` is a middleware factory. Internally it validates in one shot with `msgspec.json.decode(raw, type=Schema)`
- The validated object is stored on the Context with `c.set_validated("json", obj)` and retrieved by its type with `c.req.valid(T)` (`c.req.valid(T, target="query")` disambiguates when two targets share a type). The core provides only this intake point and does not validate anything itself
- The core has no third-party dependencies. `c.json()` encodes through the app's JSON codec: the standard library by default, or `MsgspecCodec` (`App(json_codec=MsgspecCodec())`), which encodes Structs directly without building dicts. Validation stays in honoka-msgspec
- **Caveat about msgspec**: A Struct's constructor does not validate (only `decode`/`convert` do). We operate under the contract that "a body obtained via the framework is always validated"
- Validation targets: `json` / `query` / `param` / `header` / `cookie` / `form`
- Users who dislike msgspec can write their own version, such as a Pydantic one (the design is swappable)
- **Non-goal**: Pre-validation on the Rust side. Rejected to avoid splitting the ownership of "correctness" across layers (kept only as a note for future consideration)

## 7. OpenAPI and Documentation (honoka-openapi)

- **Scalar is the default documentation UI** (Swagger UI is not used)
- `docs(app, title=..., version=...)` from `honoka-openapi` serves `/docs` (Scalar) and `/openapi.json`. The document is built per request from `app.route_table()`, so later routes appear too. Scalar is loaded from a version-pinned CDN URL with a Subresource Integrity hash (self-hosting is a parameter). Route options `status=` and `responses={404: ErrorBody}` document the success status and error bodies; `openapi=False` hides a route
- Collecting schema information is the plugin's responsibility: the `validator()` middleware holds the schema as metadata, and the OpenAPI plugin walks `app.route_table()` to gather it. The core is merely a carrier that does not interpret the contents
- Response schemas come from the handler's return annotation (`Json[User]` → the 200 response schema), read with `typing.get_type_hints`. Error responses are declared with a route option (e.g., `responses={404: ErrorBody}`)
- JSON Schema generation is **for documentation only** and is not used for runtime validation

## 8. Type System Policy

- **Target checkers**: Astral's ty and Meta's Pyrefly at their strictest settings. mypy is out of scope, and we do not depend on any plugin mechanism
- Only standard features are used: PEP 695 type parameters (with PEP 696 defaults), Protocol, and ParamSpec (when needed). Python 3.13 or later
- `c.req.valid(T)` returns the concrete type `T`, and unvalidated access returns `object`; nothing passes through `Any` (avoiding the Hono v5 `unknown` problem)
- CI: three stages — `ty check --error all` + `pyrefly check --preset strict` + `honoka check`

### Items to verify (to be resolved first in the PoC)

1. Whether msgspec Structs are handled correctly by ty/Pyrefly at their strictest settings (inference of `__init__`). If not, switch to plain dataclasses plus our own conversion layer
2. Whether `Key[T]`, `c.get`/`c.set`, and `c.json[T] -> Json[T]` (including checking a returned `Json[X]` against a `-> Json[User]` annotation) behave as intended in both checkers. Keep a pair of files, `ok.py` (must pass) and `bad.py` (must fail: wrong value for a key, wrong return type, attribute typo), and run both checkers on them

## 9. Agent-First Features

### 9.1 `honoka check` (framework-specific linter)

Provided by the separate package `honoka-cli` (which installs the `honoka` command), so the core stays free of tooling. It mechanically detects discrepancies between types and runtime behavior, and mistakes specific to the framework:

```bash
honoka check main.py
# ✗ POST /users: c.req.valid(UserIn) is called, but the route validates CreateUser
# ✗ GET /users/{id}: path parameter id is never referenced
```

- Duplicate routes, mismatched param types, `c.req.valid(T)` calls for types the route does not validate, and the like raise at startup/check time. This guarantees that "you notice a mistake the moment you write it"
- Implemented by importing the app and inspecting `app.route_table()` (plus a small AST visit of handler bodies for `c.req.valid(...)` calls); it is not a general-purpose linter

### 9.2 Machine-readable errors

```json
{
  "error": "validation_failed",
  "fields": [
    {"path": "age", "expected": "integer >= 0", "received": -3}
  ],
  "schema": "/openapi.json#/components/schemas/CreateUser"
}
```

A format that lets an agent autonomously follow "what is wrong, how to fix it, and where the schema is".

### 9.3 Other

- **llms.txt**: Provide the documentation itself in llms.txt format so that it can be pasted into an agent's context. For apps built with honoka, the separate package `honoka-llms` serves `/llms.txt` (index) and `/llms-full.txt` (parameters, bodies, responses) generated from the route table; Scalar's API Reference has no such feature
- **Official middleware** (Idempotency-Key, rate limiting, API-key authentication, CSRF, CORS, ...): one separate package each (`honoka-cors`, `honoka-csrf`, ...), none part of the core (see 9.4)
- **SSE**: Easily stream progress with `c.stream(async_generator)`
- **MCP exposure (separate package, future)**: Setting `tool=True` on a route also exposes it as an MCP tool, preventing duplicate HTTP and MCP implementations

### 9.4 Official middleware (one package each)

Selected from Hono's built-in set (basic-auth, bearer-auth, body-limit, cache, combine, compress, context-storage, cors, csrf, etag, ip-restriction, jwk, jwt, language, logger, method-override, mount, request-id, secure-headers, serve-static, timeout, timing, trailing-slash, ...) plus agent-first additions. Every middleware is its own package named `honoka-<name>` (import `honoka_<name>`), so an app installs only what it uses, each can carry its own dependencies, and none of it enters the core. Packages may depend on each other when one reads another's typed key (`honoka-logger` reads the id from `honoka-request-id`).

| Tier | Middleware | Notes |
|---|---|---|
| v1 | `logger`, `request_id`, `cors`, `csrf`, `secure_headers`, `body_limit`, `timeout`, `timing`, `trailing_slash` | `csrf` follows Hono (Origin / `Sec-Fetch-Site` check for form-like content types). `body_limit` must be enforced while the body is read, so that an oversized body is rejected before it is all in memory. `timing` emits `Server-Timing`. 405/HEAD/OPTIONS handling is core behavior, not middleware |
| done | `basic_auth`, `bearer_auth`, `jwt` (HS256/384/512 only; `sign`/`verify`/`decode`), `ip_restriction`, `etag`, `serve_static`, `combine` (`some`/`every`/`except_`), `compress` (gzip/deflate), `method_override` (an ASGI wrapper, because routing happens before middleware), `pretty_json`, `powered_by`, `language`; `mount` is in the core | Authentication takes a user-supplied verifier and sets a typed key |
| Not implemented | `jwk` and RS/ES/EdDSA JWT (need a crypto library), `cache` (the Workers Cache API: an adapter concern), brotli/zstd in `compress` | Add as separate packages when a dependency is acceptable |
| v2 (agent-first) | `idempotency_key`, `rate_limit`, `api_key` | They need a store (in-memory by default, pluggable); idempotency must handle concurrent duplicates and payload mismatches |
| Not adopted | `context_storage` (typed keys replace it), `jsx_renderer` (no template DSL) | Not needed for an API-first framework |

### 9.5 Helpers

Selected from Hono's helpers (accepts, adapter, conninfo, cookie, css, dev, factory, html, proxy, route, ssg, streaming, testing, websocket, plus the JWT helper). Helpers that every app needs are methods on `Context`/`Request` in the core; the rest are not adopted or live in separate packages.

| Tier | Helper | Form |
|---|---|---|
| v1 (core) | cookie | `c.req.cookie(name)`, `c.cookie(name, value, ...)`, `c.delete_cookie(name)` |
| v1 (core) | accepts | `c.req.accepts(["application/json", "text/html"])` for content negotiation |
| v1 (core) | conninfo | `c.req.client` (remote address from the ASGI scope) |
| v1 (core) | route | `c.route` (the matched route template, e.g. `/users/{user_id}`), used by logger/metrics so that labels stay low-cardinality |
| v1 (core) | html | `c.html(str)` (needed for the docs page; no template DSL) |
| v1 (core) | file | `c.file(path, root=..., filename=...)`: read into memory for now (see 4.3). Pass `root` when `path` comes from a request, so `../` and absolute paths are a 404 |
| v1 (core) | streaming | `c.stream(...)`, `c.stream_text(...)`, and `c.sse(events)` (Server-Sent Events: a `str` or `SSEEvent(data, event, id, retry)` per event; names with line breaks are rejected), chunks sent as produced |
| v1 (CLI) | dev | `honoka routes` prints `app.route_table()` (Hono's `showRoutes`) |
| v0/v1 | testing | `app.test()` (see 12.3) |
| v1 (core) | cookie extras | `c.cookie(..., expires, priority, partitioned, prefix="secure"\|"host")` with Hono's rules (400-day limit, `__Host-` needs path `/` and no domain), `c.signed_cookie(name, value, secret)` / `c.req.signed_cookie(name, secret)` (HMAC-SHA256; `False` when the signature fails), `generate_cookie(...)`, `c.req.cookie()` for all cookies, `c.delete_cookie(...)` returning the old value |
| v1 (core) | request | `c.req.header()` / `c.req.cookie()` / `c.req.query()` / `c.req.queries()` without a name return everything; `c.req.url`, `c.req.text()`, `c.req.parse_body(all=, dot=)` (urlencoded and multipart, files as `UploadedFile`), `c.req.scope` (the raw ASGI scope, for adapters) |
| v1 (core) | exception | `HTTPError(status, code, detail, response=Response(...))` carries its own response (Hono's `HTTPException` `res`); `err.get_response()` |
| v1 (core) | websocket | `@app.ws(path)` over the ASGI websocket scope (5.1) |
| Not adopted | factory | Decorators return the function unchanged and `Context` has no type parameters, so `Handler`, `Middleware`, and `Next` type aliases are enough |
| Not adopted | adapter | One app object with two entries (4.3), not a per-platform adapter package |
| Not adopted | css, ssg, proxy | Outside an API-first framework; a proxy needs an HTTP client dependency |

## 10. Package Layout

```
honoka            … routing, Context, middleware execution, responses (no third-party dependencies)
honoka-cli        … the `honoka` command: `honoka routes`, `honoka check`
honoka-msgspec    … validator(), structured 400
honoka-openapi    … schema collection, /openapi.json, Scalar serving
honoka-<name>     … one package per official middleware (9.4): request-id, logger, timing, secure-headers, cors, csrf, body-limit, timeout, trailing-slash, ...
honoka-mcp        … MCP exposure (future)
```

## 11. Roadmap

- **v0 (PoC)**
  1. Verify msgspec + ty/Pyrefly compatibility
  2. Verify `Key[T]`, `c.req.valid(T)`, and `Json[T]` in both checkers (`ok.py` / `bad.py`)
  3. Implement the Context over the ASGI scope
  4. Build the benchmark harness (see 11.1) and a minimal implementation (static routing, `Json[T]` returns, a `Context` that can be constructed without a server); measure the cost of the Context abstraction against bare ASGI and the other frameworks
- **v1**: Complete the core (middleware, error handling, synchronous handlers), honoka-msgspec, honoka-openapi (Scalar), honoka check, the v1 middleware packages of 9.4, a minimal `app.test()`, the ASGI entry (4.1)
- **v2**: the v2 middleware packages (authentication, idempotency, rate limiting, ...), full test client
- **v3 and later**: Formal design of WebSocket/SSE, honoka-mcp

### 11.1 Benchmark policy

The benchmarks must be runnable by anyone with one command and must compare honoka with other frameworks.

- **Harness**: a uv workspace member `benchmarks/`. One app per framework in `benchmarks/apps/<framework>/`, and a runner that starts each app under identical conditions, drives it with a load generator (`oha` or `wrk`), and writes JSON plus a Markdown table
- **Frameworks**: bare ASGI (the baseline), honoka, FastAPI, Litestar, Starlette, Falcon, BlackSheep, Robyn
- **Server**: Granian (ASGI) for everything except Robyn, so that the framework is the only variable. A second run uses each framework's recommended server
- **Scenarios**: (1) plaintext `/health`; (2) fixed JSON response; (3) path parameter; (4) JSON body validation plus JSON response; (5) middleware stack (logger + CORS + request-id); (6) the same with a simulated 5 ms async I/O, to show the real-world gap
- **Metrics**: RPS, p50/p99 latency, CPU time per request, RSS. Fixed worker count, warm-up, median of N runs; record hardware, versions, and git SHA
- **Fairness**: each app is written idiomatically with equivalent validation and no logging. Results are committed as artifacts
- **Gate**: CI checks honoka against bare ASGI (budget decided after the first baseline), never against other frameworks

## 12. Open Questions

1. **Context design for WebSocket/SSE**: Fold it into the HTTP req/res model, or make it a separate abstraction
2. **Packaging/build**: Whether to build a Rust core (via maturin) all the way to custom routing, or to stay pure Python on top of ASGI. Build v0 in pure Python first and decide based on profiling
3. **Test client `app.test()`**: Whether a Context can be constructed without a server. A litmus test for whether the Context abstraction is sound
4. **Treatment of DI**: In principle, we do not have it. Request scope is the Context; globals are published with `app.provide(KEY, value)` and read with `c.get(KEY)` (typed keys, see 5.2). Revisit if requested
5. ~~Naming~~ → **honoka** decided (PyPI availability confirmed. A double meaning: a tribute to Hono (炎, "flame"), and the design philosophy that ほのか (honoka) means "faint, slight, thin")
6. **`call_next()` vs `call_next(c)`**: The first allocates a closure per request per middleware; the second lets the chain be composed once at registration. Decide with the v0 benchmark

## 13. Rejected Alternatives (Record)

| Alternative | Reason for rejection |
|---|---|
| FastAPI-style argument DI | Contradicts Context-first; poor type expressiveness |
| Adopting Pydantic as the validator | 6–15× slower than msgspec. It also avoids defining Structs twice, so msgspec has the edge |
| Rust-side pre-validation | Ownership of "correctness" splits across three layers, making debugging difficult. The complexity never gets contained |
| Built-in validation in the core | Violates Hono's "thin core" principle. Middleware is sufficient |
| RSGI as a second interface | Only Granian speaks it, Workers cannot; no measurable gain once the core reads ASGI directly (2.3) |
| Swagger UI | Scalar is adopted instead |
| mypy plugins | Violates the design requirement of expressing everything with standard type features |
| Automatic inference of Context type parameters (TS-style) | Limits of the Python type system |
| `Context[Env, Body]` type parameters | `c.set` stays untyped, one slot cannot hold six validation targets, and a mismatch with the decorator passes silently with a wrong type. Replaced by typed keys and `c.req.valid(T)` (principle 8) |
| Handlers returning bare `Response` | The response type is lost, so OpenAPI cannot document responses. Replaced by `Json[T]` |

## 14. Reference Implementations

- **ASGI specification and Granian**: The server interface honoka targets; Granian is the Rust server it is tuned on
- **Robyn**: A framework built on PyO3 + tokio. A reference for the messy details of bridging event loops
- **Hono**: The canonical reference for API design (Context, onion middleware, validator)
- **Litestar**: A msgspec-native framework that ships a Scalar plugin; the closest existing alternative, so it is a required benchmark and comparison target
