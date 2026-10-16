"""timeout."""

import asyncio

from honoka_timeout import timeout

from honoka import App, Context, Response


def test_a_slow_handler_is_cut_off_with_a_504() -> None:
    app = App()
    app.use("*", timeout(0.05))

    @app.get("/slow")
    async def slow(c: Context) -> Response:
        await asyncio.sleep(0.5)
        return c.text("late")

    @app.get("/fast")
    async def fast(c: Context) -> Response:
        return c.text("ok")

    with app.test() as client:
        slow_resp = client.get("/slow")
        fast_resp = client.get("/fast")

    assert (slow_resp.status, slow_resp.json()) == (504, {"error": "timeout"})
    assert fast_resp.text == "ok"
