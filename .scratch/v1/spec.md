# honoka v1: minimum production-ready release

Source of truth: `rfc/honoka-framework-rfc.md` (RFC). This spec only fixes scope and order.

## Goal
Pure-Python core on Granian (RSGI) plus honoka-msgspec, honoka-middleware (v1 tier of RFC 9.4),
honoka-openapi, `honoka check` / `honoka routes`, and a benchmark harness. Strictly typed
(`ty check --error all`, `pyrefly check --preset strict`), ruff width 100, Python >= 3.13.

## Out of scope
Rust core, ASGI adapter, WebSocket/SSE formal design, v2 middleware tiers, MCP, PyPI publishing.

## Decisions
- Tracker: local markdown here. No remote, so no PR; commit per seam on `main`.
- Tests: pytest. Public boundary = `app.test()` and a fake RSGI proto; a few real-Granian integration tests.
- Context has no type parameters; types ride on `Key[T]`, `c.req.valid(T)`, `Json[T]` (RFC principle 8).
- `def` handlers run in a worker thread by default; `inline=True` is opt-in (RFC principle 3).
- `call_next()` vs `call_next(c)` is decided by the benchmark (issue 10); start with `call_next()`.

## Acceptance (v1 done)
Every issue below is `resolved`; full test suite, ruff, ty, pyrefly, and `honoka check` pass;
benchmark baseline recorded.
