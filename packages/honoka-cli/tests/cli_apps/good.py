from cli_apps.validators import FakeValidator
from honoka import App, Context, Response


class CreateUser: ...


app = App()


@app.get("/health")
def health(c: Context) -> Response:
    return c.text("ok")


@app.get("/users/{user_id}")
def get_user(c: Context) -> Response:
    return c.text(str(c.req.param("user_id", int)))


@app.post("/users", FakeValidator(json=CreateUser))
def create_user(c: Context) -> Response:
    c.req.valid(CreateUser)
    return c.text("created", 201)
