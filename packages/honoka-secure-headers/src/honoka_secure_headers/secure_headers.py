from honoka import Context, Middleware, Next, Response

DEFAULTS: dict[str, str] = {
    "x_content_type_options": "nosniff",
    "x_frame_options": "SAMEORIGIN",
    "referrer_policy": "no-referrer",
    "strict_transport_security": "max-age=15552000; includeSubDomains",
    "cross_origin_opener_policy": "same-origin",
    "cross_origin_resource_policy": "same-origin",
}


def secure_headers(**overrides: str | None) -> Middleware:
    """Add security headers unless the handler already set them. Pass `name=value` to replace a
    default or `name=None` to drop it (e.g. `x_frame_options=None`)."""
    unknown = sorted(set(overrides) - set(DEFAULTS))
    if unknown:
        raise TypeError(f"unknown secure header option(s): {unknown}; known: {sorted(DEFAULTS)}")
    merged = {**DEFAULTS, **overrides}
    pairs = [(k.replace("_", "-"), v) for k, v in merged.items() if v is not None]

    async def middleware(c: Context, call_next: Next) -> Response:
        response = await call_next()
        present = {k.lower() for k, _ in response.headers}
        response.headers.extend((k, v) for k, v in pairs if k not in present)
        return response

    return middleware
