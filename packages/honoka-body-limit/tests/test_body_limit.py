"""body_limit."""

from honoka_body_limit import body_limit

from honoka import App, Context, Response


def test_a_declared_content_length_over_the_limit_is_rejected_before_the_handler() -> None:
    app = App()
    app.use("*", body_limit(5))
    called: list[str] = []

    async def handler(c: Context) -> Response:
        called.append("handler")
        return c.text("ok")

    app.post("/", handler)

    with app.test() as client:
        resp = client.post("/", content=b"123456", headers={"content-length": "6"})

    assert (resp.status, resp.json()) == (413, {"error": "payload_too_large"})
    assert called == []


def test_a_body_that_outgrows_the_limit_while_streaming_is_rejected_when_read() -> None:
    app = App()
    app.use("*", body_limit(5))

    async def handler(c: Context) -> Response:
        return c.text(str(len(await c.req.body())))

    app.post("/", handler)

    with app.test() as client:
        too_big = client.post("/", content=b"0123456789")
        fits = client.post("/", content=b"01234")

    assert too_big.status == 413
    assert fits.text == "5"
