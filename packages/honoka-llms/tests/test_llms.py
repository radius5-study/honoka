"""llms.txt and llms-full.txt, generated from the routes and observed over the test client."""

from typing import Annotated

import msgspec
from honoka_llms import llms

from honoka import App, Context, Json, Response
from honoka_msgspec import validator


class CreateUser(msgspec.Struct):
    name: Annotated[str, msgspec.Meta(min_length=1)]
    age: Annotated[int, msgspec.Meta(ge=0)]


class UserOut(msgspec.Struct):
    id: int
    name: str


class Search(msgspec.Struct):
    q: str
    page: int = 1


def make_app(**options: str) -> App:
    app = App()
    llms(app, title="Demo API", **options)

    @app.post("/users", validator(json=CreateUser, query=Search), status=201)
    async def create_user(c: Context) -> Json[UserOut]:
        """Create a user.

        The user is stored and returned."""
        return c.json(UserOut(id=1, name="a"), 201)

    @app.get("/health")
    def health(c: Context) -> Response:
        return c.text("ok")

    return app


def fetch(app: App, path: str) -> tuple[str, str]:
    with app.test() as client:
        resp = client.get(path)
    return resp.headers["content-type"], resp.text


def test_the_index_lists_every_endpoint_with_its_summary_and_a_link_into_the_spec() -> None:
    content_type, text = fetch(make_app(), "/llms.txt")
    assert content_type == "text/markdown; charset=utf-8"
    lines = text.splitlines()
    assert lines[0] == "# Demo API"
    assert "- [POST /users](/openapi.json#/paths/~1users/post): Create a user." in lines
    assert "- [GET /health](/openapi.json#/paths/~1health/get)" in lines
    assert (
        "- [OpenAPI document](/openapi.json): every endpoint and schema, machine-readable" in lines
    )
    assert "/llms.txt" not in text.split("## Optional")[0]


def test_the_description_becomes_a_blockquote_only_when_given() -> None:
    assert (
        "> Orders for the shop"
        in fetch(make_app(description="Orders for the shop"), "/llms.txt")[1]
    )
    assert ">" not in fetch(make_app(), "/llms.txt")[1].split("## Endpoints")[0]


def test_the_full_file_documents_parameters_bodies_and_responses() -> None:
    text = fetch(make_app(), "/llms-full.txt")[1]
    assert "## POST /users" in text
    assert "The user is stored and returned." in text
    assert "- `q` (query, string, required)" in text
    assert "- `page` (query, integer, optional)" in text
    assert "Request body (application/json): `CreateUser`" in text
    assert "  - `name`: string, required, minLength 1" in text
    assert "  - `age`: integer, required, minimum 0" in text
    assert "- 201: `UserOut`: OK" in text
    assert "- 400: `ValidationFailed`: Validation failed" in text
    assert "- 200: OK" in text


def test_routes_registered_after_llms_still_appear() -> None:
    app = App()
    llms(app, title="Late")

    @app.get("/late")
    def late(c: Context) -> Response:
        return c.text("")

    assert "GET /late" in fetch(app, "/llms.txt")[1]
