import json

from honoka import Context, Json, Middleware, Next, Response


def pretty_json(*, space: int = 2, query: str = "pretty", force: bool = False) -> Middleware:
    """Re-render JSON responses indented by `space` when the request has the `query` flag
    (any value, even empty), or always with `force=True`. Other responses are untouched."""

    async def middleware(c: Context, call_next: Next) -> Response:
        response = await call_next()
        if isinstance(response, Json) and (force or c.req.query(query) is not None):
            response.body = json.dumps(response.data, indent=space, ensure_ascii=False).encode()
        return response

    return middleware


__all__ = ["pretty_json"]
