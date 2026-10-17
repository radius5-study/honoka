from cli_apps.validators import FakeValidator
from honoka import App, Context, Response


class CreateUser: ...


class UserIn: ...


app = App()


def unused_parameter(c: Context) -> Response:
    return c.text("never reads the id")


app.get("/users/{id}", unused_parameter)


def typo_in_parameter_name(c: Context) -> Response:
    return c.text(c.req.param("ordr_id"))


app.get("/orders/{order_id}", typo_in_parameter_name)


def wrong_validated_type(c: Context) -> Response:
    c.req.valid(UserIn)
    return c.text("")


app.post("/users", FakeValidator(json=CreateUser), wrong_validated_type)


def typed_unused(c: Context) -> Response:
    return c.text("never reads n")


app.get("/typed/{n:int}", typed_unused)


def no_return_annotation(c: Context):  # type: ignore[no-untyped-def]
    return c.text("")


app.get("/legacy", no_return_annotation)
