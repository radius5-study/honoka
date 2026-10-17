from honoka_cors import cors
from honoka_request_id import request_id

from honoka import App, Context, HTTPError, Json, Response
from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN
from honoka_msgspec import MsgspecCodec, validator

app = App(json_codec=MsgspecCodec())
app.use("*", request_id())
app.use("*", cors(origins=[ORIGIN]))


def require_auth(c: Context) -> None:
    if not shop.authorized(c.req.header("authorization")):
        raise HTTPError(401, "unauthorized")


def health(c: Context) -> Response:
    return c.text("ok")


app.get("/health", health, inline=True)


async def products(c: Context) -> Json[dict[str, object]]:
    await shop.database(shop.READ_LATENCY)
    return c.json(shop.list_products_for(c.req.valid(shop.ProductQuery)))


app.get("/products", validator(query=shop.ProductQuery), products)


async def product(c: Context) -> Json[dict[str, object]]:
    await shop.database(shop.READ_LATENCY)
    try:
        return c.json(shop.get_product(c.req.param("product_id", int)))
    except shop.ShopError as exc:
        raise HTTPError(exc.status, exc.code) from exc


app.get("/products/{product_id}", product)


async def create_order(c: Context) -> Json[dict[str, object]]:
    require_auth(c)
    await shop.database(shop.WRITE_LATENCY)
    try:
        return c.json(shop.create_order_for(c.req.valid(shop.NewOrder)), 201)
    except shop.ShopError as exc:
        raise HTTPError(exc.status, exc.code) from exc


app.post("/orders", validator(json=shop.NewOrder), create_order)


async def order(c: Context) -> Json[dict[str, object]]:
    require_auth(c)
    await shop.database(shop.READ_LATENCY)
    try:
        return c.json(shop.get_order(c.req.param("order_id", int)))
    except shop.ShopError as exc:
        raise HTTPError(exc.status, exc.code) from exc


app.get("/orders/{order_id}", order)
