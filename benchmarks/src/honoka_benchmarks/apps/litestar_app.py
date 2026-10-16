import uuid
from typing import Annotated, Literal

from litestar import Litestar, Request, get, post
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotAuthorizedException
from litestar.params import Parameter
from litestar.response import Response
from litestar.types import ASGIApp, Message, Receive, Scope, Send

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN

Category = Literal["books", "games", "garden", "grocery", "home", "music", "sports", "toys"]


def request_id(app: ASGIApp) -> ASGIApp:
    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", uuid.uuid4().hex.encode()),
                ]
            await send(message)

        await app(scope, receive, wrapped)

    return middleware


def check_auth(request: Request[object, object, State]) -> None:
    if not shop.authorized(request.headers.get("authorization")):
        raise NotAuthorizedException("unauthorized")


@get("/health", sync_to_thread=False)
def health() -> Response[str]:
    return Response("ok", media_type="text/plain")


@get("/products")
async def products(
    category: Category | None = None,
    min_price: Annotated[float | None, Parameter(ge=0)] = None,
    max_price: Annotated[float | None, Parameter(ge=0)] = None,
    sort: Literal["name", "price", "-price"] = "name",
    page: Annotated[int, Parameter(ge=1)] = 1,
    per_page: Annotated[int, Parameter(ge=1, le=100)] = 50,
) -> dict[str, object]:
    await shop.database(shop.READ_LATENCY)
    return shop.list_products(category, min_price, max_price, sort, page, per_page)


@get("/products/{product_id:int}")
async def product(product_id: int) -> dict[str, object]:
    await shop.database(shop.READ_LATENCY)
    try:
        return shop.get_product(product_id)
    except shop.ShopError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@post("/orders")
async def create_order(
    request: Request[object, object, State], data: shop.NewOrder
) -> dict[str, object]:
    check_auth(request)
    await shop.database(shop.WRITE_LATENCY)
    try:
        return shop.create_order_for(data)
    except shop.ShopError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@get("/orders/{order_id:int}")
async def order(request: Request[object, object, State], order_id: int) -> dict[str, object]:
    check_auth(request)
    await shop.database(shop.READ_LATENCY)
    try:
        return shop.get_order(order_id)
    except shop.ShopError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


app = Litestar(
    [health, products, product, create_order, order],
    middleware=[request_id],
    cors_config=CORSConfig(allow_origins=[ORIGIN]),
    logging_config=None,
)
