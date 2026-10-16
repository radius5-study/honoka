"""The OpenAPI document and the Scalar page, observed over the public test client."""

from typing import Annotated, cast

import msgspec

from honoka import App, Context, Json, Response
from honoka_msgspec import validator
from honoka_openapi import SCALAR_URL, docs


class CreateUser(msgspec.Struct):
    name: Annotated[str, msgspec.Meta(min_length=1)]
    age: int


class UserOut(msgspec.Struct):
    id: int
    name: str


class Search(msgspec.Struct):
    q: str
    page: int = 1


class ErrorBody(msgspec.Struct):
    error: str


def at(node: object, *path: str | int) -> object:
    for step in path:
        if isinstance(step, str):
            assert isinstance(node, dict)
            node = cast(dict[str, object], node)[step]
        else:
            assert isinstance(node, list)
            node = cast(list[object], node)[step]
    return node


def keys(node: object) -> list[str]:
    assert isinstance(node, dict)
    return sorted(cast(dict[str, object], node))


def make_app() -> App:
    app = App()
    docs(app, title="Demo", version="1.2.3")

    @app.post(
        "/users", validator(json=CreateUser, query=Search), status=201, responses={404: ErrorBody}
    )
    async def create_user(c: Context) -> Json[UserOut]:
        """Create a user.

        The user is stored and returned."""
        return c.json(UserOut(id=1, name="a"), 201)

    @app.get("/users/{user_id}")
    def get_user(c: Context) -> Json[UserOut]:
        return c.json(UserOut(id=1, name="a"))

    @app.get("/health")
    def health(c: Context) -> Response:
        return c.text("ok")

    return app


def spec_of(app: App) -> object:
    with app.test() as client:
        return client.get("/openapi.json").json()


def test_the_document_describes_the_api_and_hides_its_own_routes() -> None:
    spec = spec_of(make_app())
    assert at(spec, "openapi") == "3.1.0"
    assert at(spec, "info") == {"title": "Demo", "version": "1.2.3"}
    assert isinstance(at(spec, "paths"), dict)
    assert keys(at(spec, "paths")) == ["/health", "/users", "/users/{user_id}"]


def test_a_docstring_becomes_the_summary_and_description() -> None:
    op = at(spec_of(make_app()), "paths", "/users", "post")
    assert at(op, "operationId") == "create_user"
    assert at(op, "summary") == "Create a user."
    assert at(op, "description") == "The user is stored and returned."


def test_a_json_validator_becomes_the_request_body_with_its_constraints() -> None:
    spec = spec_of(make_app())
    body = at(spec, "paths", "/users", "post", "requestBody")
    assert at(body, "required") is True
    assert at(body, "content", "application/json", "schema") == {
        "$ref": "#/components/schemas/CreateUser"
    }
    assert at(spec, "components", "schemas", "CreateUser", "properties", "name", "minLength") == 1


def test_a_query_validator_becomes_query_parameters_with_required_flags() -> None:
    parameters = at(spec_of(make_app()), "paths", "/users", "post", "parameters")
    assert parameters == [
        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}},
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1},
        },
    ]


def test_path_parameters_are_documented_even_without_a_validator() -> None:
    parameters = at(spec_of(make_app()), "paths", "/users/{user_id}", "get", "parameters")
    assert parameters == [
        {"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}
    ]


def test_the_return_annotation_becomes_the_success_response() -> None:
    spec = spec_of(make_app())
    ref = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/UserOut"}}}}
    assert at(spec, "paths", "/users/{user_id}", "get", "responses", "200") == {
        "description": "OK",
        **ref,
    }
    assert at(spec, "paths", "/users", "post", "responses", "201") == {"description": "OK", **ref}
    assert at(spec, "paths", "/health", "get", "responses", "200") == {"description": "OK"}


def test_declared_error_responses_and_the_validation_failure_are_documented() -> None:
    responses = at(spec_of(make_app()), "paths", "/users", "post", "responses")
    assert at(responses, "404", "content", "application/json", "schema") == {
        "$ref": "#/components/schemas/ErrorBody"
    }
    assert at(responses, "400", "content", "application/json", "schema") == {
        "$ref": "#/components/schemas/ValidationFailed"
    }


def test_routes_registered_after_docs_still_appear() -> None:
    app = App()
    docs(app)

    @app.get("/late")
    def late(c: Context) -> Response:
        return c.text("")

    assert "/late" in keys(at(spec_of(app), "paths"))


def test_the_docs_page_loads_a_pinned_scalar_with_an_integrity_hash() -> None:
    app = make_app()
    with app.test() as client:
        resp = client.get("/docs")
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert 'data-url="/openapi.json"' in resp.text
    assert f'src="{SCALAR_URL}"' in resp.text
    assert "@scalar/api-reference@1.75.0" in SCALAR_URL
    assert 'integrity="sha384-' in resp.text
    assert "<title>Demo</title>" in resp.text


def test_the_page_escapes_the_title() -> None:
    app = App()
    docs(app, title="<script>x</script>")
    with app.test() as client:
        assert "<script>x</script>" not in client.get("/docs").text
