import msgspec
import pytest

from honoka import Context, Json, Key


class User(msgspec.Struct):
    name: str


USER: Key[User] = Key("user")


def test_a_value_set_under_a_key_is_returned_by_get() -> None:
    c = Context()
    user = User(name="a")
    c.set(USER, user)
    assert c.get(USER) is user


def test_get_of_a_key_that_was_never_set_names_the_key() -> None:
    with pytest.raises(LookupError, match="'user'"):
        Context().get(USER)


def test_two_keys_with_the_same_name_are_distinct_slots() -> None:
    other: Key[User] = Key("user")
    c = Context()
    c.set(USER, User(name="a"))
    with pytest.raises(LookupError):
        c.get(other)


def test_json_response_encodes_a_struct_without_a_dict() -> None:
    resp = Context().json(User(name="é"), status=201)
    assert resp.status == 201
    assert resp.body == b'{"name":"\xc3\xa9"}'
    assert ("content-type", "application/json") in resp.headers
    assert resp.data == User(name="é")


def test_json_does_not_mutate_the_headers_the_caller_passed() -> None:
    headers = [("x-a", "1")]
    Json(User(name="a"), headers=headers)
    assert headers == [("x-a", "1")]
