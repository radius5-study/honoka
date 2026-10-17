from honoka import App, Context, Response
from honoka_llms.render import render_full, render_index
from honoka_openapi import build

_MARKDOWN = "text/markdown; charset=utf-8"


def llms(
    app: App,
    *,
    title: str,
    description: str | None = None,
    version: str = "0.1.0",
    path: str = "/llms.txt",
    full_path: str = "/llms-full.txt",
    spec_path: str = "/openapi.json",
) -> None:
    """Serve `path` (an index of endpoints) and `full_path` (everything an agent needs to call
    them), generated from the route table on each request, so later routes appear too.

    The index links into the OpenAPI document at `spec_path`; mount it with
    `honoka_openapi.docs`. Routes with `openapi=False` are left out."""

    def respond(c: Context, text: str) -> Response:
        return Response(text.encode(), 200, [("content-type", _MARKDOWN)])

    async def index(c: Context) -> Response:
        document = build(app, title=title, version=version)
        return respond(
            c,
            render_index(
                document,
                title=title,
                description=description,
                spec_path=spec_path,
                full_path=full_path,
            ),
        )

    app.get(path, index, openapi=False)

    async def full(c: Context) -> Response:
        document = build(app, title=title, version=version)
        return respond(c, render_full(document, title=title, description=description))

    app.get(full_path, full, openapi=False)
