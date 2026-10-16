import uuid

import msgspec
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN


class RequestId:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"].append((b"x-request-id", uuid.uuid4().hex.encode()))
            await send(message)

        await self.app(scope, receive, wrapped)


def fail(exc: shop.ShopError) -> Response:
    return JSONResponse({"error": exc.code}, exc.status)


async def health(request: Request) -> Response:
    return PlainTextResponse("ok")


async def products(request: Request) -> Response:
    try:
        query = shop.parse_product_query(request.query_params)
    except msgspec.ValidationError:
        return JSONResponse({"error": "validation_failed"}, 400)
    await shop.database(shop.READ_LATENCY)
    return JSONResponse(shop.list_products_for(query))


async def product(request: Request) -> Response:
    await shop.database(shop.READ_LATENCY)
    try:
        return JSONResponse(shop.get_product(request.path_params["product_id"]))
    except shop.ShopError as exc:
        return fail(exc)


async def create_order(request: Request) -> Response:
    if not shop.authorized(request.headers.get("authorization")):
        return JSONResponse({"error": "unauthorized"}, 401)
    try:
        order = shop.decode_order(await request.body())
    except (msgspec.ValidationError, msgspec.DecodeError):
        return JSONResponse({"error": "validation_failed"}, 400)
    await shop.database(shop.WRITE_LATENCY)
    try:
        return JSONResponse(shop.create_order_for(order), 201)
    except shop.ShopError as exc:
        return fail(exc)


async def order(request: Request) -> Response:
    if not shop.authorized(request.headers.get("authorization")):
        return JSONResponse({"error": "unauthorized"}, 401)
    await shop.database(shop.READ_LATENCY)
    try:
        return JSONResponse(shop.get_order(request.path_params["order_id"]))
    except shop.ShopError as exc:
        return fail(exc)


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/products", products),
        Route("/products/{product_id:int}", product),
        Route("/orders", create_order, methods=["POST"]),
        Route("/orders/{order_id:int}", order),
    ],
    middleware=[Middleware(CORSMiddleware, allow_origins=[ORIGIN]), Middleware(RequestId)],
)
