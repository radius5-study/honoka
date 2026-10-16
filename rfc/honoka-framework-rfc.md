# RFC: honoka — A Thin Python Web Framework with a Rust Core and Schema-Driven Design

- Status: Draft
- Created: 2026-10-16
- Intended readers: implementing agents / co-developers

---

## 1. Overview

We will build a web framework that brings the philosophy of Hono (JS) — "a thin core, Context-centric, middleware-oriented, and not forcing unnecessary things on the user" — to Python. The primary target runtime is Granian (RSGI); by pushing routing and I/O down into Rust, we aim to break through the performance ceiling of FastAPI-class frameworks. At the same time, we adopt an "agent-first" stance: the goal is a framework that is **easy for AI agents to write**, and that produces APIs that are **easy for agents to use as clients**.

## 2. Background and Motivation

### 2.1 Problems with existing frameworks

- **FastAPI**: The de facto standard, but its RPS is low (the combined cost of decorator introspection, Pydantic, and DI); it has no concept of a request context (the context is scattered across function-argument DI); and type expressions such as `Annotated[Query, Depends(...)]` are poor
- **Swapping the server (uvicorn → Granian) does not solve it**: Synthetic benchmarks show multi-fold differences, but in real applications (DB-bound) the difference is within 5–10%. The bottleneck remains in the framework layer
- **Hono v5**: Raising type safety (e.g., `c.req.json()` now returning `unknown`) has come at the expense of writing ergonomics. There is room on the Python side to achieve equivalent safety with better DX

### 2.2 Technical foundations

- **ASGI**: The community-driven de facto specification between server and application. Compatibility is high, but it carries structural overhead from round-tripping event dicts
- **RSGI**: Granian's own specification for the Rust⇄Python boundary. Rust owns I/O and threading, and a synchronous response (`proto.response_str`) lets the application reply without running the event loop
- **Granian**: A server written in Rust (tokio + hyper + PyO3). Supports ASGI/WSGI/RSGI, HTTP/1 and HTTP/2, and WebSocket, and has a production track record. Not supported: HTTP/3, trio/gevent, and some ASGI extensions

## 3. Design Principles

1. **Context first**: Handlers take only a `Context` argument. This eliminates DI hell
2. **No dicts at the boundary**: Keep RSGI's `scope` as a handle to the Rust-side object, and convert it to Python lazily on access (lazy binding)
3. **Synchronous handlers are first-class**: Handlers that are not async are called directly, without running the event loop, and ride on RSGI's `response_str`
4. **Keep the core thin**: The core contains no validation, OpenAPI, or authentication. All of these are middleware/plugins
5. **Validation is a single layer**: msgspec only. Do not split it across multiple layers (e.g., Rust-side validation), which would multiply the owners of "correctness"
6. **Standard type features only**: Do not rely on mypy plugins. Passing under the strictest settings of ty and Pyrefly is a design requirement
7. **Agent-first**: No implicit context, startup-time validation, machine-readable errors, and llms.txt

## 4. Architecture

```
Client
   ↕ HTTP/1 and HTTP/2, WebSocket
Granian (Rust: tokio + hyper)
   ↕ RSGI (primary target) / ASGI (compatibility layer)
honoka core
   ├─ Routing (Rust side or Python side; see Open Questions)
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

### 4.2 Responsibility boundaries on the Rust side

- **Does**: routing, holding the Context object, emitting responses (the HTTP layer is Granian)
- **Does not**: validation (does not look inside the JSON), business logic

### 4.3 ASGI adapter

- Provided as a "works, but slow" compatibility layer. Converts via `Context.from_asgi(scope, receive, send)`
- On the ASGI path the lazy-binding optimization has no effect (the input is already Python objects), but API compatibility is preserved
- An on-ramp for adoption strategy. RSGI is recommended for production

## 5. Core API

### 5.1 Routing and handlers

```python
from honoka import App, Context, Response

app = App()


# Synchronous handler (does not run the event loop)
@app.get("/health")
def health(c: Context) -> Response:
    return c.text("ok")


# Asynchronous handler
@app.get("/users/{user_id}")
async def get_user(c: Context) -> Response:
    user_id: int = c.req.param("user_id", int)  # with parsing and type conversion
    user = await db.fetch(user_id)
    return c.json({"user": user})
```

- Decorators return the function **as is, without wrapping it** (so that types are not broken)
- The handler signature is unified as `(c: Context) -> Response`

### 5.2 Context

```python
class Context[E: Mapping[str, object] = EmptyEnv, B = None]:
    req: Request  # access to header/query/param/body (lazy binding)

    @property
    def env(self) -> E: ...  # typed with a TypedDict. A nonexistent key is a type error
    def set(self, key: str, value: object) -> None: ...
```

- Env is declared explicitly by the user as a `TypedDict`. `c.env["user_id"]` is strictly checked with literal keys (guaranteed by ty/Pyrefly features)
- Type parameters do not change meaning by position. Write them directly, as in `Context[AppEnv, CreateUser]` (no wrapper types are introduced)
- Declaring the same thing twice — once in the decorator arguments and once in the handler's type annotation — is accepted, and the two are cross-checked by `honoka check` (see below)

### 5.3 Middleware

Hono's onion model:

```python
async def auth(c: Context, next: Next) -> Response:
    token = c.req.header("authorization")
    c.set("user_id", verify(token))
    return await next()


app.use("/api/*", logger())
app.use("/admin/*", auth)
```

- Path matching is completed in the core (Rust side); only the execution chain drops down into Python

### 5.4 Error handling

```python
@app.on_error
async def on_error(err: Exception, c: Context) -> Response: ...


@app.not_found
def not_found(c: Context) -> Response: ...
```

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
async def create_user(c: Context[EmptyEnv, CreateUser]) -> Response:
    body = c.req.body  # of type CreateUser, already validated. Does not pass through unknown
    ...
```

Implementation approach:

- `validator()` is a middleware factory. Internally it validates in one shot with `msgspec.json.decode(raw, type=Schema)`
- The validated object is stored on the Context with `c.set_validated("json", obj)` and retrieved with `c.req.body`. The core provides only this intake point and does not depend on msgspec
- **Caveat about msgspec**: A Struct's constructor does not validate (only `decode`/`convert` do). We operate under the contract that "a body obtained via the framework is always validated"
- Validation targets: `json` / `query` / `param` / `header` / `cookie` / `form`
- Users who dislike msgspec can write their own version, such as a Pydantic one (the design is swappable)
- **Non-goal**: Pre-validation on the Rust side. Rejected to avoid splitting the ownership of "correctness" across layers (kept only as a note for future consideration)

## 7. OpenAPI and Documentation (honoka-openapi)

- **Scalar is the default documentation UI** (Swagger UI is not used)
- `app.docs()` serves `/docs` (Scalar) and `/openapi.json`
- Collecting schema information is the plugin's responsibility: the `validator()` middleware holds the schema as metadata, and the OpenAPI plugin walks `app.route_table()` to gather it. The core is merely a carrier that does not interpret the contents
- JSON Schema generation is **for documentation only** and is not used for runtime validation

## 8. Type System Policy

- **Target checkers**: Astral's ty and Meta's Pyrefly at their strictest settings. mypy is out of scope, and we do not depend on any plugin mechanism
- Only standard features are used: PEP 695 type parameters, TypedDict, Protocol, and ParamSpec (when needed)
- `c.req.body` returns a concrete type and never passes through `unknown`/`Any` (avoiding the Hono v5 `unknown` problem)
- CI: three stages — `ty check --strictest` + `pyrefly check --strict` + `honoka check`

### Items to verify (to be resolved first in the PoC)

1. Whether msgspec Structs are handled correctly by ty/Pyrefly at their strictest settings (inference of `__init__`). If not, switch to plain dataclasses plus our own conversion layer
2. Whether generic inference for `Context[E, B]` works as intended in both checkers

## 9. Agent-First Features

### 9.1 `honoka check` (framework-specific linter)

Mechanically detects discrepancies between types and runtime behavior, and mistakes specific to the framework:

```bash
honoka check main.py
# ✗ POST /users: validator(json=CreateUser) is declared, but Context's type parameter is UserIn
# ✗ GET /users/{id}: path parameter id is never referenced
```

- Duplicate routes, mismatched param types, undefined Context variables, and the like raise at startup/check time. This guarantees that "you notice a mistake the moment you write it"

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
- **Idempotency-Key**: Built-in middleware (assuming agents retry multiple times)
- **Rate limiting / API-key authentication**: Built-in middleware (not part of the core)
- **SSE**: Easily stream progress with `c.stream(async_generator)`
- **MCP exposure (separate package, future)**: Setting `tool=True` on a route also exposes it as an MCP tool, preventing duplicate HTTP and MCP implementations

## 10. Package Layout

```
honoka            … routing, Context, middleware execution, responses, honoka check
honoka-msgspec    … validator(), structured 400
honoka-openapi    … schema collection, /openapi.json, Scalar serving
honoka-mcp        … MCP exposure (future)
```

## 11. Roadmap

- **v0 (PoC)**
  1. Verify msgspec + ty/Pyrefly compatibility
  2. Verify inference of the Context generics
  3. Implement the lazy-binding Context on Granian/RSGI
  4. Build a minimal implementation with static routing + dict returns and benchmark it (compare against Granian + bare RSGI and Robyn; measure the "cost of the Context abstraction")
- **v1**: Complete the core (middleware, error handling, synchronous handlers), honoka-msgspec, honoka-openapi (Scalar), honoka check
- **v2**: ASGI adapter, built-in middleware suite (authentication, idempotency, rate limiting), test client
- **v3 and later**: Formal design of WebSocket/SSE, honoka-mcp

## 12. Open Questions

1. **Context design for WebSocket/SSE**: Fold it into the HTTP req/res model, or make it a separate abstraction
2. **Packaging/build**: Whether to build a Rust core (via maturin) all the way to custom routing, or to stay pure Python on top of Granian's RSGI. Build v0 in pure Python first and decide based on profiling
3. **Test client `app.test()`**: Whether a Context can be constructed without RSGI. A litmus test for whether the Context abstraction is sound
4. **Treatment of DI**: In principle, we do not have it. Request scope is the Context; globals are injected through the app constructor. Revisit if requested
5. ~~Naming~~ → **honoka** decided (PyPI availability confirmed. A double meaning: a tribute to Hono (炎, "flame"), and the design philosophy that ほのか (honoka) means "faint, slight, thin")

## 13. Rejected Alternatives (Record)

| Alternative | Reason for rejection |
|---|---|
| FastAPI-style argument DI | Contradicts Context-first; poor type expressiveness |
| Adopting Pydantic as the validator | 6–15× slower than msgspec. It also avoids defining Structs twice, so msgspec has the edge |
| Rust-side pre-validation | Ownership of "correctness" splits across three layers, making debugging difficult. The complexity never gets contained |
| Built-in validation in the core | Violates Hono's "thin core" principle. Middleware is sufficient |
| Swagger UI | Scalar is adopted instead |
| mypy plugins | Violates the design requirement of expressing everything with standard type features |
| Automatic inference of Context type parameters (TS-style) | Limits of the Python type system. Replaced by explicit declaration plus cross-checking with honoka check |

## 14. Reference Implementations

- **Granian / RSGI specification**: A prior answer to the Rust⇄Python boundary. Essential reading as design material
- **Robyn**: A framework built on PyO3 + tokio. A reference for the messy details of bridging event loops
- **Hono**: The canonical reference for API design (Context, onion middleware, validator)
