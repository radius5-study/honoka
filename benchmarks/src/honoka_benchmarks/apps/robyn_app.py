"""Robyn runs its own server: `python -m honoka_benchmarks.apps.robyn_app` (PORT env)."""

import os
import uuid

import msgspec
from robyn import ALLOW_CORS, Request, Response, Robyn

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN

TEXT = {"Content-Type": "text/plain; charset=utf-8"}
APPLICATION_JSON = {"Content-Type": "application/json"}
encode = msgspec.json.encode

app = Robyn(__file__)
ALLOW_CORS(app, [ORIGIN])


@app.after_request()
def request_id(response: Response) -> Response:
    response.headers.set("x-request-id", uuid.uuid4().hex)
    return response


def reply(status: int, payload: object) -> Response:
    return Response(status, APPLICATION_JSON, encode(payload).decode())


@app.get("/health")
async def health() -> Response:
    return Response(200, TEXT, "ok")


@app.get("/products")
async def products(request: Request) -> Response:
    try:
        raw = request.query_params.to_dict()
        query = shop.parse_product_query({k: v[0] for k, v in raw.items()})
    except msgspec.ValidationError:
        return reply(400, {"error": "validation_failed"})
    await shop.database(shop.READ_LATENCY)
    return reply(200, shop.list_products_for(query))


@app.get("/products/:product_id")
async def product(request: Request) -> Response:
    await shop.database(shop.READ_LATENCY)
    try:
        return reply(200, shop.get_product(int(request.path_params["product_id"])))
    except shop.ShopError as exc:
        return reply(exc.status, {"error": exc.code})
    except ValueError:
        return reply(400, {"error": "invalid_parameter"})


@app.post("/orders")
async def create_order(request: Request) -> Response:
    if not shop.authorized(request.headers.get("authorization")):
        return reply(401, {"error": "unauthorized"})
    try:
        raw = request.body
        order = shop.decode_order(raw if isinstance(raw, bytes) else str(raw).encode())
    except (msgspec.ValidationError, msgspec.DecodeError):
        return reply(400, {"error": "validation_failed"})
    await shop.database(shop.WRITE_LATENCY)
    try:
        return reply(201, shop.create_order_for(order))
    except shop.ShopError as exc:
        return reply(exc.status, {"error": exc.code})


@app.get("/orders/:order_id")
async def order(request: Request) -> Response:
    if not shop.authorized(request.headers.get("authorization")):
        return reply(401, {"error": "unauthorized"})
    await shop.database(shop.READ_LATENCY)
    try:
        return reply(200, shop.get_order(int(request.path_params["order_id"])))
    except shop.ShopError as exc:
        return reply(exc.status, {"error": exc.code})
    except ValueError:
        return reply(400, {"error": "invalid_parameter"})


if __name__ == "__main__":
    app.start(host="127.0.0.1", port=int(os.environ["PORT"]))
