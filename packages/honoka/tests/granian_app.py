"""The app served by real Granian in test_granian.py."""

from collections.abc import AsyncIterator
from pathlib import Path

from honoka import App, Context, Json, Key, Next, Response

DB: Key[str] = Key("db")


app = App()


@app.on_startup
def connect() -> None:
    app.provide(DB, "pool")


async def stamp(c: Context, call_next: Next) -> Response:
    response = await call_next()
    response.headers.append(("x-seen-status", str(response.status)))
    return response


app.use("*", stamp)


def health(c: Context) -> Response:
    return c.text("ok")


app.get("/health", health)


async def get_user(c: Context) -> Json[dict[str, object]]:
    return c.json({"id": c.req.param("user_id", int), "name": c.get(DB)})


app.get("/users/{user_id}", get_user)


async def echo(c: Context) -> Response:
    return c.json({"query": c.req.query("q"), "body": await c.req.json()})


app.post("/echo", echo)


def cookie(c: Context) -> Response:
    c.cookie("sid", "abc", max_age=60)
    return c.text(c.req.cookie("in") or "none")


app.get("/cookie", cookie)


async def stream(c: Context) -> Response:
    async def chunks() -> AsyncIterator[str]:
        for part in ("a", "b", "c"):
            yield part

    return c.stream(chunks(), content_type="text/plain")


app.get("/stream", stream)


def boom(c: Context) -> Response:
    raise RuntimeError("secret")


app.get("/boom", boom)


def file(c: Context) -> Response:
    return c.file(Path(__file__).parent / "data" / "hello.txt", filename="greeting.txt")


app.get("/file", file)


def typed(c: Context) -> Response:
    return c.text(str(c.req.param("n", int) + 1))


app.get("/typed/{n:int}", typed)


def wild(c: Context) -> Response:
    return c.text(c.req.path)


app.get("/wild/*", wild)
