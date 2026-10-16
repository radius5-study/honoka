# RFC: honoka — A Thin, Strictly Typed Python Web Framework on Granian (RSGI)

- Status: Draft
- Created: 2026-10-16
- Intended readers: implementing agents / co-developers

---

## 1. Overview

We will build a web framework that brings the philosophy of Hono (JS) — "a thin core, Context-centric, middleware-oriented, and not forcing unnecessary things on the user" — to Python. The primary target runtime is Granian (RSGI); Granian already keeps I/O in Rust, so honoka itself stays pure Python for v0/v1 and minimizes per-request framework overhead through registration-time precompilation and strict typing. Routing moves to Rust only if benchmarks justify it (see 12.2). At the same time, we adopt an "agent-first" stance: the goal is a framework that is **easy for AI agents to write**, and that produces APIs that are **easy for agents to use as clients**.

## 2. Background and Motivation

### 2.1 Problems with existing frameworks

- **FastAPI**: The de facto standard, but its RPS is low (the combined cost of decorator introspection, Pydantic, and DI); it has no concept of a request context (the context is scattered across function-argument DI); and type expressions such as `Annotated[Query, Depends(...)]` are poor
- **Swapping the server (uvicorn → Granian) does not solve it by itself**: Synthetic benchmarks show multi-fold differences, but in real applications (DB-bound) the difference is within 5–10%. That also bounds what any framework swap can gain, so honoka does not sell raw speed alone: the goal is to keep framework overhead as close as possible to a bare RSGI app, and to prove it with benchmarks against other frameworks (see 11.1)
- **Hono v5**: Raising type safety (e.g., `c.req.json()` now returning `unknown`) has come at the expense of writing ergonomics. There is room on the Python side to achieve equivalent safety with better DX

### 2.2 Technical foundations

- **ASGI**: The community-driven de facto specification between server and application. Compatibility is high, but it carries structural overhead from round-tripping event dicts
- **RSGI**: Granian's own specification for the Rust⇄Python boundary. Rust owns I/O and threading, and a synchronous response (`proto.response_str`) lets the application reply without awaiting anything
- **Granian**: A server written in Rust (tokio + hyper + PyO3). Supports ASGI/WSGI/RSGI, HTTP/1 and HTTP/2, and WebSocket, and has a production track record. Not supported: HTTP/3, trio/gevent, and some ASGI extensions

## 3. Design Principles

1. **Context first**: Handlers take only a `Context` argument. This eliminates DI hell
2. **No dicts at the boundary**: Keep RSGI's `scope` as a handle to the Rust-side object, and convert it to Python lazily on access (lazy binding)
3. **Synchronous handlers are supported, and safe by default**: `def` handlers run in a worker thread so that blocking I/O cannot stall the event loop. Calling them inline on the loop (which rides on RSGI's `response_str`) is an explicit opt-in (`inline=True`), added only after the PoC benchmark shows it matters
4. **Keep the core thin**: The core contains no schema validation, OpenAPI, or authentication. These are middleware/plugins; each official middleware is its own package, e.g. `honoka-cors` (see 9.4). The core has no third-party dependencies: JSON goes through a pluggable codec (`App(json_codec=...)`), which is the standard library by default and `MsgspecCodec` from honoka-msgspec for Structs
5. **Validation is a single layer**: msgspec only. Do not split it across multiple layers (e.g., Rust-side validation), which would multiply the owners of "correctness". The core only converts scalar path/query parameters (`c.req.param(name, type)`); that is parsing, not schema validation
6. **Standard type features only**: Do not rely on mypy plugins. Passing under the strictest settings of ty and Pyrefly is a design requirement
7. **Agent-first**: No implicit context, startup-time checks for everything introspectable (duplicate routes, unused path parameters, missing type hints), `honoka check` in CI for the rest, machine-readable errors, and llms.txt
8. **Types ride on values, not on `Context`**: `Context` has no type parameters. Types flow through typed keys (`Key[T]`), type arguments (`c.req.valid(T)`), and return types (`Json[T]`), so that every read and write is checked and the checkers need only simple generics

## 4. Architecture

```
Client
   ↕ HTTP/1 and HTTP/2, WebSocket
Granian (Rust: tokio + hyper)
   ↕ RSGI (primary target) / ASGI (compatibility layer)
honoka core
   ├─ Routing (pure Python in v0/v1; Rust only if benchmarks justify it, see 12.2)
   ├─ Context (lazy binding)
   └─ Middleware onion execution
   ↕
honoka-msgspec / honoka-openapi / other plugins
```

### 4.1 Mapping to RSGI

```python
async def rsgi_app(scope, proto):
    ctx = Context(scope, proto)  # scope is a handle to the Rust-side object
    handler = router.match(scope.method, scope.path)
    resp = await dispatch(ctx, handler, middlewares)
    resp.emit(
        proto
    )  # choose response_str / response_bytes / response_stream / response_file depending on the response type
```

### 4.2 Responsibility boundaries if a Rust core is adopted (conditional, see 12.2)

- **Does**: routing, holding the Context object, emitting responses (the HTTP layer is Granian)
- **Does not**: validation (does not look inside the JSON), business logic

### 4.3 ASGI adapter

- Provided as a "works, but slow" compatibility layer. Converts via `Context.from_asgi(scope, receive, send)`
- On the ASGI path the lazy-binding optimization has no effect (the input is already Python objects), but API compatibility is preserved
- An on-ramp for adoption strategy. RSGI is recommended for production

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
- Route metadata: decorators accept extra keyword options (e.g., `responses={...}`, `tool=True`) and store them in `app.route_table()`. The core never interprets them; plugins do

### 5.2 Context

```python
class Key[T]:
    def __init__(self, name: str) -> None: ...


class Context:  # no type parameters
    req: Request  # access to header/query/param/body (lazy binding)

    def set[T](self, key: Key[T], value: T) -> None: ...
    def get[T](self, key: Key[T]) -> T: ...  # raises LookupError if the key was never set
    def json[T](self, obj: T, status: int = 200) -> Json[T]: ...
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
@app.on_error
async def on_error(c: Context) -> Response: ...  # the error is read from c.error


@app.not_found
def not_found(c: Context) -> Response: ...
```

- Handlers signal errors by raising `HTTPError(status, code, detail=None)`, which the core renders in the machine-readable format below (`code` becomes the `error` field). `ValidationFailed` is a subclass of `HTTPError`, defined in the core and raised by honoka-msgspec
- `on_error` takes only the Context (principle 1), as in Hono v5 (`c.error`)

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

Mechanically detects discrepancies between types and runtime behavior, and mistakes specific to the framework:

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

- **llms.txt**: Provide the documentation itself in llms.txt format so that it can be pasted into an agent's context
- **Official middleware** (Idempotency-Key, rate limiting, API-key authentication, CSRF, CORS, ...): one separate package each (`honoka-cors`, `honoka-csrf`, ...), none part of the core (see 9.4)
- **SSE**: Easily stream progress with `c.stream(async_generator)`
- **MCP exposure (separate package, future)**: Setting `tool=True` on a route also exposes it as an MCP tool, preventing duplicate HTTP and MCP implementations

### 9.4 Official middleware (one package each)

Selected from Hono's built-in set (basic-auth, bearer-auth, body-limit, cache, combine, compress, context-storage, cors, csrf, etag, ip-restriction, jwk, jwt, language, logger, method-override, mount, request-id, secure-headers, serve-static, timeout, timing, trailing-slash, ...) plus agent-first additions. Every middleware is its own package named `honoka-<name>` (import `honoka_<name>`), so an app installs only what it uses, each can carry its own dependencies, and none of it enters the core. Packages may depend on each other when one reads another's typed key (`honoka-logger` reads the id from `honoka-request-id`).

| Tier | Middleware | Notes |
|---|---|---|
| v1 | `logger`, `request_id`, `cors`, `csrf`, `secure_headers`, `body_limit`, `timeout`, `timing`, `trailing_slash` | `csrf` follows Hono (Origin / `Sec-Fetch-Site` check for form-like content types). `body_limit` must be enforced while the body is read, because RSGI hands over the full body. `timing` emits `Server-Timing`. 405/HEAD/OPTIONS handling is core behavior, not middleware |
| v2 | `basic_auth`, `bearer_auth`, `jwt`/`jwk`, `ip_restriction`, `etag`/`cache`, `serve_static`, `combine` (some/every/except), `compress` (a reverse proxy is preferred), `mount` (with the ASGI adapter) | Authentication takes a user-supplied verifier and sets a typed key |
| v2 (agent-first) | `idempotency_key`, `rate_limit`, `api_key` | They need a store (in-memory by default, pluggable); idempotency must handle concurrent duplicates and payload mismatches |
| Not adopted | `context_storage` (typed keys replace it), `jsx_renderer`, `language`, `method_override`, `powered_by`, `pretty_json` | Not needed for an API-first framework |

### 9.5 Helpers

Selected from Hono's helpers (accepts, adapter, conninfo, cookie, css, dev, factory, html, proxy, route, ssg, streaming, testing, websocket, plus the JWT helper). Helpers that every app needs are methods on `Context`/`Request` in the core; the rest are not adopted or live in separate packages.

| Tier | Helper | Form |
|---|---|---|
| v1 (core) | cookie | `c.req.cookie(name)`, `c.cookie(name, value, ...)`, `c.delete_cookie(name)` |
| v1 (core) | accepts | `c.req.accepts(["application/json", "text/html"])` for content negotiation |
| v1 (core) | conninfo | `c.req.client` (remote address from the RSGI scope) |
| v1 (core) | route | `c.route` (the matched route template, e.g. `/users/{user_id}`), used by logger/metrics so that labels stay low-cardinality |
| v1 (core) | html | `c.html(str)` (needed for the docs page; no template DSL) |
| v1 (core) | streaming | `c.stream(...)` over `response_stream`; the SSE helper `c.sse(...)` follows in v2 |
| v1 (CLI) | dev | `honoka routes` prints `app.route_table()` (Hono's `showRoutes`) |
| v0/v1 | testing | `app.test()` (see 12.3) |
| v2 (separate packages) | signed cookies, JWT `sign`/`verify`/`decode` | Take a secret/key supplied by the user |
| v3 | websocket | Formal design with WebSocket/SSE (see 12.1) |
| Not adopted | factory | Decorators return the function unchanged and `Context` has no type parameters, so `Handler`, `Middleware`, and `Next` type aliases are enough |
| Not adopted | adapter | Granian only; the ASGI adapter is a separate concern (4.3) |
| Not adopted | css, ssg, proxy | Outside an API-first framework; a proxy needs an HTTP client dependency |

## 10. Package Layout

```
honoka            … routing, Context, middleware execution, responses, honoka check
honoka-msgspec    … validator(), structured 400
honoka-openapi    … schema collection, /openapi.json, Scalar serving
honoka-<name>     … one package per official middleware (9.4): request-id, logger, timing, secure-headers, cors, csrf, body-limit, timeout, trailing-slash, ...
honoka-mcp        … MCP exposure (future)
```

## 11. Roadmap

- **v0 (PoC)**
  1. Verify msgspec + ty/Pyrefly compatibility
  2. Verify `Key[T]`, `c.req.valid(T)`, and `Json[T]` in both checkers (`ok.py` / `bad.py`)
  3. Implement the lazy-binding Context on Granian/RSGI
  4. Build the benchmark harness (see 11.1) and a minimal implementation (static routing, `Json[T]` returns, a `Context` that can be constructed without RSGI); measure the cost of the Context abstraction against bare RSGI and the other frameworks
- **v1**: Complete the core (middleware, error handling, synchronous handlers), honoka-msgspec, honoka-openapi (Scalar), honoka check, the v1 middleware packages of 9.4, a minimal `app.test()`
- **v2**: ASGI adapter, the v2 middleware packages (authentication, idempotency, rate limiting, ...), full test client
- **v3 and later**: Formal design of WebSocket/SSE, honoka-mcp

### 11.1 Benchmark policy

The benchmarks must be runnable by anyone with one command and must compare honoka with other frameworks.

- **Harness**: a uv workspace member `benchmarks/`. One app per framework in `benchmarks/apps/<framework>/`, and a runner that starts each app under identical conditions, drives it with a load generator (`oha` or `wrk`), and writes JSON plus a Markdown table
- **Frameworks**: bare Granian RSGI (the baseline), honoka, FastAPI, Litestar, Starlette, Falcon, BlackSheep, Robyn
- **Server**: Granian for everything except Robyn (RSGI for honoka and the baseline, ASGI for the others), so that the framework is the only variable. A second run uses each framework's recommended server
- **Scenarios**: (1) plaintext `/health`; (2) fixed JSON response; (3) path parameter; (4) JSON body validation plus JSON response; (5) middleware stack (logger + CORS + request-id); (6) the same with a simulated 5 ms async I/O, to show the real-world gap
- **Metrics**: RPS, p50/p99 latency, CPU time per request, RSS. Fixed worker count, warm-up, median of N runs; record hardware, versions, and git SHA
- **Fairness**: each app is written idiomatically with equivalent validation and no logging. Results are committed as artifacts
- **Gate**: CI checks honoka against bare RSGI (budget decided after the first baseline), never against other frameworks

## 12. Open Questions

1. **Context design for WebSocket/SSE**: Fold it into the HTTP req/res model, or make it a separate abstraction
2. **Packaging/build**: Whether to build a Rust core (via maturin) all the way to custom routing, or to stay pure Python on top of Granian's RSGI. Build v0 in pure Python first and decide based on profiling
3. **Test client `app.test()`**: Whether a Context can be constructed without RSGI. A litmus test for whether the Context abstraction is sound
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
| Swagger UI | Scalar is adopted instead |
| mypy plugins | Violates the design requirement of expressing everything with standard type features |
| Automatic inference of Context type parameters (TS-style) | Limits of the Python type system |
| `Context[Env, Body]` type parameters | `c.set` stays untyped, one slot cannot hold six validation targets, and a mismatch with the decorator passes silently with a wrong type. Replaced by typed keys and `c.req.valid(T)` (principle 8) |
| Handlers returning bare `Response` | The response type is lost, so OpenAPI cannot document responses. Replaced by `Json[T]` |

## 14. Reference Implementations

- **Granian / RSGI specification**: A prior answer to the Rust⇄Python boundary. Essential reading as design material
- **Robyn**: A framework built on PyO3 + tokio. A reference for the messy details of bridging event loops
- **Hono**: The canonical reference for API design (Context, onion middleware, validator)
- **Litestar**: A msgspec-native framework that ships a Scalar plugin; the closest existing alternative, so it is a required benchmark and comparison target
