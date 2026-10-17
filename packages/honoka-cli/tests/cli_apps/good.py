from cli_apps.validators import FakeValidator
from honoka import App, Context, Response


class CreateUser: ...


app = App()


def health(c: Context) -> Response:
    return c.text("ok")


app.get("/health", health)


def get_user(c: Context) -> Response:
    return c.text(str(c.req.param("user_id", int)))


app.get("/users/{user_id}", get_user)


def create_user(c: Context) -> Response:
    c.req.valid(CreateUser)
    return c.text("created", 201)


app.post("/users", FakeValidator(json=CreateUser), create_user)


def get_order(c: Context) -> Response:
    return c.text(str(c.req.param("order_id", int)))


app.get("/orders/{order_id:int}", get_order)
