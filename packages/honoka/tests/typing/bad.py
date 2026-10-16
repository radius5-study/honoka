"""Every line marked `expect-error` must be flagged by both checkers, and no other line."""

import msgspec

from honoka import Context, Json, Key


class User(msgspec.Struct):
    name: str


class Other(msgspec.Struct):
    id: int


USER: Key[User] = Key("user")


def wrong_value_for_key(c: Context) -> None:
    c.set(USER, 1)  # expect-error


def wrong_body_type(c: Context) -> Json[User]:
    return c.json(Other(id=1))  # expect-error


def dict_is_not_a_user(c: Context) -> Json[User]:
    return c.json({"name": "a"})  # expect-error


def attribute_typo(c: Context) -> str:
    return c.get(USER).nme  # expect-error
