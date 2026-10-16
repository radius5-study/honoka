"""Baseline for RSGI frameworks: a hand-written RSGI app."""

from honoka._rsgi import Proto, Scope
from honoka_benchmarks.apps.raw_core import extra_headers, handle


async def app(scope: Scope, proto: Proto) -> None:
    status, body, content_type = await handle(
        scope.method,
        scope.path,
        scope.query_string,
        scope.headers.get("authorization"),
        proto,
    )
    proto.response_bytes(
        status, [("content-type", content_type), *extra_headers(scope.headers.get("origin"))], body
    )
