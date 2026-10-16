"""The app served by real Granian in test_granian.py."""

from collections.abc import AsyncIterator

import msgspec

from honoka import App, Context, Json, Key, Next, Response

DB: Key[str] = Key("db")


class User(msgspec.Struct):
    id: int
    name: str


app = App()


@app.on_startup
def connect() -> None:
    app.provide(DB, "pool")


async def stamp(c: Context, call_next: Next) -> Response:
    response = await call_next()
    response.headers.append(("x-seen-status", str(response.status)))
    return response


app.use("*", stamp)


@app.get("/health")
def health(c: Context) -> Response:
    return c.text("ok")


@app.get("/users/{user_id}")
async def get_user(c: Context) -> Json[User]:
    return c.json(User(id=c.req.param("user_id", int), name=c.get(DB)))


@app.post("/echo")
async def echo(c: Context) -> Response:
    return c.json({"query": c.req.query("q"), "body": await c.req.json()})


@app.get("/cookie")
def cookie(c: Context) -> Response:
    c.cookie("sid", "abc", max_age=60)
    return c.text(c.req.cookie("in") or "none")


@app.get("/stream")
async def stream(c: Context) -> Response:
    async def chunks() -> AsyncIterator[str]:
        for part in ("a", "b", "c"):
            yield part

    return c.stream(chunks(), content_type="text/plain")


@app.get("/boom")
def boom(c: Context) -> Response:
    raise RuntimeError("secret")
