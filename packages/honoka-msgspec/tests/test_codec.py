"""With MsgspecCodec, Structs are encoded directly; the core default cannot do that."""

import msgspec

from honoka import App, Context, Json
from honoka_msgspec import MsgspecCodec


class User(msgspec.Struct):
    name: str


def test_a_struct_is_encoded_directly_when_the_app_uses_the_msgspec_codec() -> None:
    app = App(json_codec=MsgspecCodec())

    @app.get("/")
    def handler(c: Context) -> Json[User]:
        return c.json(User(name="é"), 201)

    with app.test() as client:
        resp = client.get("/")

    assert (resp.status, resp.body) == (201, b'{"name":"\xc3\xa9"}')


def test_the_default_codec_reports_a_struct_as_a_500_instead_of_guessing() -> None:
    app = App()

    @app.get("/")
    def handler(c: Context) -> Json[User]:
        return c.json(User(name="a"))

    with app.test() as client:
        resp = client.get("/")

    assert (resp.status, resp.json()) == (500, {"error": "internal_error"})
