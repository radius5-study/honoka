Status: deferred
Type: task

# 10: Benchmark harness and baseline

benchmarks/ workspace member per RFC 11.1; bare RSGI baseline + honoka + FastAPI/Litestar/Starlette/Falcon/BlackSheep/Robyn; record baseline; decide call_next().

Refs: RFC (rfc/honoka-framework-rfc.md), .scratch/v1/spec.md

## Outcome (2026-10-17)

Harness, eight conforming apps (incl. raw-asgi), a multi-process load generator,
README output and an in-process profiler are done. Throughput on the author's laptop varied up to
3x between identical runs, so no baseline or budget was recorded and the `call_next()` question is
left open. Revisit on a quiet dedicated machine; use `python -m honoka_benchmarks.profile` for
stable per-request CPU numbers in the meantime.
