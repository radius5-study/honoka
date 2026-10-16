import uuid

import falcon
import falcon.asgi
import msgspec
from falcon.asgi import Request, Response

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN


class RequestId:
    async def process_response(
        self, req: Request, resp: Response, resource: object, req_succeeded: bool
    ) -> None:
        resp.set_header("x-request-id", uuid.uuid4().hex)


def fail(resp: Response, status: int, code: str) -> None:
    resp.status = status
    resp.media = {"error": code}


class Health:
    async def on_get(self, req: Request, resp: Response) -> None:
        resp.content_type = "text/plain; charset=utf-8"
        resp.text = "ok"


class Products:
    async def on_get(self, req: Request, resp: Response) -> None:
        try:
            query = shop.parse_product_query(
                {k: v if isinstance(v, str) else v[0] for k, v in req.params.items()}
            )
        except msgspec.ValidationError:
            fail(resp, 400, "validation_failed")
            return
        await shop.database(shop.READ_LATENCY)
        resp.media = shop.list_products_for(query)


class Product:
    async def on_get(self, req: Request, resp: Response, product_id: int) -> None:
        await shop.database(shop.READ_LATENCY)
        try:
            resp.media = shop.get_product(product_id)
        except shop.ShopError as exc:
            fail(resp, exc.status, exc.code)


class Orders:
    async def on_post(self, req: Request, resp: Response) -> None:
        if not shop.authorized(req.get_header("authorization")):
            fail(resp, 401, "unauthorized")
            return
        try:
            order = shop.decode_order(await req.bounded_stream.read())
        except (msgspec.ValidationError, msgspec.DecodeError):
            fail(resp, 400, "validation_failed")
            return
        await shop.database(shop.WRITE_LATENCY)
        try:
            resp.media = shop.create_order_for(order)
            resp.status = falcon.HTTP_201
        except shop.ShopError as exc:
            fail(resp, exc.status, exc.code)


class Order:
    async def on_get(self, req: Request, resp: Response, order_id: int) -> None:
        if not shop.authorized(req.get_header("authorization")):
            fail(resp, 401, "unauthorized")
            return
        await shop.database(shop.READ_LATENCY)
        try:
            resp.media = shop.get_order(order_id)
        except shop.ShopError as exc:
            fail(resp, exc.status, exc.code)


app = falcon.asgi.App(
    middleware=[falcon.CORSMiddleware(allow_origins=[ORIGIN]), RequestId()],
)
app.add_route("/health", Health())
app.add_route("/products", Products())
app.add_route("/products/{product_id:int}", Product())
app.add_route("/orders", Orders())
app.add_route("/orders/{order_id:int}", Order())
