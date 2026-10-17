import html

from honoka import App, Context, Response
from honoka_openapi.document import build

SCALAR_VERSION = "1.75.0"
SCALAR_URL = f"https://cdn.jsdelivr.net/npm/@scalar/api-reference@{SCALAR_VERSION}/dist/browser/standalone.js"
SCALAR_INTEGRITY = "sha384-xgelKlDcc+BYp0O+ZYzFesLnWCgGpEbXVVZWNQT6WyZYygN01tbEMygnjPXb5OPI"


def docs(
    app: App,
    *,
    title: str = "API",
    version: str = "0.1.0",
    path: str = "/docs",
    spec_path: str = "/openapi.json",
    scalar_url: str = SCALAR_URL,
    scalar_integrity: str | None = SCALAR_INTEGRITY,
) -> None:
    """Serve the OpenAPI document at `spec_path` and a Scalar page at `path`.

    The document is built on each request from `app.route_table()`, so routes registered after
    this call still appear. Scalar is loaded from a pinned URL with a Subresource Integrity hash;
    pass your own `scalar_url` (and its hash, or `None`) to self-host."""

    async def spec(c: Context) -> Response:
        return c.json(build(app, title=title, version=version))

    app.get(spec_path, spec, openapi=False)

    async def page(c: Context) -> Response:
        integrity = (
            f' integrity="{html.escape(scalar_integrity)}" crossorigin="anonymous"'
            if scalar_integrity is not None
            else ""
        )
        return c.html(
            "<!doctype html>"
            f'<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            f'<body><script id="api-reference" data-url="{html.escape(spec_path)}"></script>'
            f'<script src="{html.escape(scalar_url)}"{integrity}></script></body></html>'
        )

    app.get(path, page, openapi=False)
