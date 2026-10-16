import uuid
from collections.abc import Awaitable, Callable

import msgspec
from blacksheep import Application, Request, Response, json, text

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN

app = Application()
app.use_cors(allow_methods="*", allow_origins=[ORIGIN], allow_headers="*")


async def request_id(
    request: Request, handler: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await handler(request)
    response.headers.add(b"x-request-id", uuid.uuid4().hex.encode())
    return response


app.middlewares.append(request_id)


def fail(exc: shop.ShopError) -> Response:
    return json({"error": exc.code}, exc.status)


def authorized(request: Request) -> bool:
    header = request.get_first_header(b"authorization")
    return shop.authorized(None if header is None else header.decode())


@app.router.get("/health")
async def health() -> Response:
    return text("ok")


@app.router.get("/products")
async def products(request: Request) -> Response:
    raw = {k: v[0] for k, v in request.query.items()}
    try:
        query = shop.parse_product_query(raw)
    except msgspec.ValidationError:
        return json({"error": "validation_failed"}, 400)
    await shop.database(shop.READ_LATENCY)
    return json(shop.list_products_for(query))


@app.router.get("/products/{int:product_id}")
async def product(product_id: int) -> Response:
    await shop.database(shop.READ_LATENCY)
    try:
        return json(shop.get_product(product_id))
    except shop.ShopError as exc:
        return fail(exc)


@app.router.post("/orders")
async def create_order(request: Request) -> Response:
    if not authorized(request):
        return json({"error": "unauthorized"}, 401)
    try:
        order = shop.decode_order(await request.read() or b"")
    except (msgspec.ValidationError, msgspec.DecodeError):
        return json({"error": "validation_failed"}, 400)
    await shop.database(shop.WRITE_LATENCY)
    try:
        return json(shop.create_order_for(order), 201)
    except shop.ShopError as exc:
        return fail(exc)


@app.router.get("/orders/{int:order_id}")
async def order(request: Request, order_id: int) -> Response:
    if not authorized(request):
        return json({"error": "unauthorized"}, 401)
    await shop.database(shop.READ_LATENCY)
    try:
        return json(shop.get_order(order_id))
    except shop.ShopError as exc:
        return fail(exc)
