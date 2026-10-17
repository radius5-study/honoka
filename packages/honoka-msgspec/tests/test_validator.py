"""validator() and c.req.valid(), observed through the public test client."""

from typing import Annotated

import msgspec
import pytest

from honoka import App, Context, Response
from honoka.testing import ClientResponse
from honoka_msgspec import validator


class CreateUser(msgspec.Struct):
    name: Annotated[str, msgspec.Meta(min_length=1, max_length=100)]
    age: Annotated[int, msgspec.Meta(ge=0)]


class Batch(msgspec.Struct):
    users: list[CreateUser]


class Search(msgspec.Struct):
    page: int = 1
    tag: list[str] = []


class RequestHeaders(msgspec.Struct):
    x_request_id: str


class Session(msgspec.Struct):
    session: str


class UserPath(msgspec.Struct):
    user_id: int


class Login(msgspec.Struct):
    user: str
    remember: bool = False


SCHEMA = "/openapi.json#/components/schemas/"


class Failure(msgspec.Struct):
    error: str
    fields: list[dict[str, object]] = []
    schema: str | None = None


def failure(resp: ClientResponse) -> Failure:
    return msgspec.json.decode(resp.body, type=Failure)


def make_app() -> App:
    app = App()
    calls: list[str] = []
    app.calls = calls  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    def create(c: Context) -> Response:
        calls.append("create")
        body = c.req.valid(CreateUser)
        query = c.req.valid(Search)
        return c.json({"name": body.name, "age": body.age, "page": query.page, "tag": query.tag})

    app.post("/users", validator(json=CreateUser, query=Search), create)

    def batch(c: Context) -> Response:
        return c.text(str(len(c.req.valid(Batch).users)))

    app.post("/batch", validator(json=Batch), batch)

    def headers(c: Context) -> Response:
        return c.text(c.req.valid(RequestHeaders).x_request_id)

    app.get("/headers", validator(header=RequestHeaders), headers)

    def cookies(c: Context) -> Response:
        return c.text(c.req.valid(Session).session)

    app.get("/cookies", validator(cookie=Session), cookies)

    def user(c: Context) -> Response:
        return c.text(str(c.req.valid(UserPath).user_id + 1))

    app.get("/users/{user_id}", validator(param=UserPath), user)

    def login(c: Context) -> Response:
        form = c.req.valid(Login)
        return c.json({"user": form.user, "remember": form.remember})

    app.post("/login", validator(form=Login), login)

    def same(c: Context) -> Response:
        try:
            c.req.valid(Search)
        except ValueError as exc:
            ambiguous = "ambiguous" in str(exc)
        else:
            ambiguous = False
        return c.json(
            {
                "ambiguous": ambiguous,
                "json": c.req.valid(Search, target="json").page,
                "query": c.req.valid(Search, target="query").page,
            }
        )

    app.post("/same", validator(json=Search, query=Search), same)

    def not_validated(c: Context) -> Response:
        try:
            c.req.valid(CreateUser)
        except LookupError as exc:
            return c.text(str(exc))
        return c.text("found")

    app.get("/not-validated", not_validated)

    return app


def test_valid_json_and_query_reach_the_handler_as_typed_objects() -> None:
    with make_app().test() as client:
        resp = client.post("/users?page=2&tag=a&tag=b", json={"name": "Ann", "age": 30})
    assert resp.json() == {"name": "Ann", "age": 30, "page": 2, "tag": ["a", "b"]}


def test_a_constraint_violation_is_a_structured_400_pointing_at_the_schema() -> None:
    app = make_app()
    with app.test() as client:
        resp = client.post("/users", json={"name": "Ann", "age": -3})
        calls = app.calls  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert resp.status == 400
    assert resp.json() == {
        "error": "validation_failed",
        "fields": [{"in": "json", "path": "age", "expected": "int >= 0", "received": -3}],
        "schema": SCHEMA + "CreateUser",
    }
    assert calls == []


def test_a_wrong_type_reports_what_was_expected_and_received() -> None:
    with make_app().test() as client:
        resp = client.post("/users", json={"name": "Ann", "age": "x"})
    assert failure(resp).fields == [
        {"in": "json", "path": "age", "expected": "int", "received": "x"}
    ]


def test_a_missing_required_field_names_the_field_without_a_received_value() -> None:
    with make_app().test() as client:
        resp = client.post("/users", json={"name": "Ann"})
    assert failure(resp).fields == [{"in": "json", "path": "age", "expected": "required field"}]


def test_nested_errors_report_the_full_path() -> None:
    with make_app().test() as client:
        resp = client.post(
            "/batch", json={"users": [{"name": "Ann", "age": 1}, {"name": "B", "age": -1}]}
        )
    assert failure(resp).fields[0]["path"] == "users[1].age"


def test_a_body_of_the_wrong_shape_is_reported_at_the_root() -> None:
    with make_app().test() as client:
        resp = client.post("/users", json=[1])
    assert failure(resp).fields == [
        {"in": "json", "path": "$", "expected": "object", "received": [1]}
    ]


def test_malformed_json_is_a_400_invalid_json() -> None:
    with make_app().test() as client:
        resp = client.post("/users", content=b"{bad")
    assert resp.status == 400
    assert failure(resp).error == "invalid_json"


def test_query_values_are_coerced_and_defaults_apply() -> None:
    with make_app().test() as client:
        resp = client.post("/users?tag=only", json={"name": "Ann", "age": 1})
    assert resp.json() == {"name": "Ann", "age": 1, "page": 1, "tag": ["only"]}


def test_an_invalid_query_value_is_reported_with_its_location() -> None:
    with make_app().test() as client:
        resp = client.post("/users?page=abc", json={"name": "Ann", "age": 1})
    assert failure(resp).fields == [
        {"in": "query", "path": "page", "expected": "int", "received": "abc"}
    ]


def test_header_names_map_to_underscored_fields() -> None:
    with make_app().test() as client:
        assert client.get("/headers", headers={"x-request-id": "r1"}).text == "r1"
        assert failure(client.get("/headers")).fields[0]["in"] == "header"


def test_cookies_are_validated() -> None:
    with make_app().test() as client:
        assert client.get("/cookies", headers={"cookie": "session=s1"}).text == "s1"
        assert client.get("/cookies").status == 400


def test_path_parameters_are_validated_and_converted() -> None:
    with make_app().test() as client:
        assert client.get("/users/6").text == "7"
        bad = client.get("/users/abc")
    assert bad.status == 400
    assert failure(bad).fields[0]["in"] == "param"


def test_urlencoded_forms_are_validated() -> None:
    with make_app().test() as client:
        resp = client.post(
            "/login",
            content=b"user=ann&remember=true",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert resp.json() == {"user": "ann", "remember": True}


def test_the_same_type_from_two_targets_needs_an_explicit_target() -> None:
    with make_app().test() as client:
        resp = client.post("/same?page=5", json={"page": 9})
    assert resp.json() == {"ambiguous": True, "json": 9, "query": 5}


def test_asking_for_a_type_the_route_did_not_validate_names_the_type() -> None:
    with make_app().test() as client:
        assert "CreateUser" in client.get("/not-validated").text


def test_a_non_struct_type_is_rejected_for_targets_that_need_fields() -> None:
    with pytest.raises(TypeError, match="query"):
        validator(query=int)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_the_validator_exposes_its_schemas_for_documentation() -> None:
    assert validator(json=CreateUser, query=Search).schemas == {
        "json": CreateUser,
        "query": Search,
    }
