"""Must pass `ty check --error all` and `pyrefly check --preset strict`."""

from dataclasses import dataclass
from typing import assert_type

from honoka import Context, Json, Key


@dataclass
class User:
    name: str


@dataclass
class Admin(User):
    level: int


USER: Key[User] = Key("user")


def reads_and_writes_typed_values(c: Context) -> Json[User]:
    c.set(USER, User(name="a"))
    user = c.get(USER)
    assert_type(user, User)
    assert_type(user.name, str)
    return c.json(user)


def subtype_body_satisfies_supertype_annotation(c: Context) -> Json[User]:
    return c.json(Admin(name="a", level=1))
