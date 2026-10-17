"""Typed keys and Json responses, observed through requests to an app."""

from dataclasses import dataclass

from honoka import App, Context, Json, Key, Response


@dataclass
class User:
    name: str


USER: Key[User] = Key("user")


def test_a_value_set_under_a_key_is_returned_by_get() -> None:
    app = App()

    def handler(c: Context) -> Response:
        c.set(USER, User(name="a"))
        return c.text(c.get(USER).name)

    app.get("/", handler)

    with app.test() as client:
        assert client.get("/").text == "a"


def test_get_of_a_key_that_was_never_set_names_the_key() -> None:
    app = App()

    def handler(c: Context) -> Response:
        try:
            c.get(USER)
        except LookupError as exc:
            return c.text(str(exc))
        return c.text("found")

    app.get("/", handler)

    with app.test() as client:
        assert "'user'" in client.get("/").text


def test_two_keys_with_the_same_name_are_distinct_slots() -> None:
    other: Key[User] = Key("user")
    app = App()

    def handler(c: Context) -> Response:
        c.set(USER, User(name="a"))
        try:
            c.get(other)
        except LookupError:
            return c.text("distinct")
        return c.text("shared")

    app.get("/", handler)

    with app.test() as client:
        assert client.get("/").text == "distinct"


def test_json_response_is_compact_utf8() -> None:
    app = App()

    def handler(c: Context) -> Json[dict[str, str]]:
        return c.json({"name": "é"}, status=201)

    app.get("/", handler)

    with app.test() as client:
        resp = client.get("/")
    assert resp.status == 201
    assert resp.body == b'{"name":"\xc3\xa9"}'


def test_json_does_not_mutate_the_headers_the_caller_passed() -> None:
    headers = [("x-a", "1")]
    Json({"a": 1}, headers=headers)
    assert headers == [("x-a", "1")]
