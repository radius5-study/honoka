import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN

Category = Literal["books", "games", "garden", "grocery", "home", "music", "sports", "toys"]


class ProductQuery(BaseModel):
    category: Category | None = None
    min_price: float | None = Field(None, ge=0)
    max_price: float | None = Field(None, ge=0)
    sort: Literal["name", "price", "-price"] = "name"
    page: int = Field(1, ge=1)
    per_page: int = Field(50, ge=1, le=100)


class Line(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=10)


class NewOrder(BaseModel):
    customer_id: int = Field(ge=1)
    lines: list[Line] = Field(min_length=1, max_length=5)
    note: str | None = Field(None, max_length=200)
    coupon: str | None = Field(None, pattern=r"^[A-Z0-9]{4,12}$")


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN])


@app.middleware("http")
async def request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["x-request-id"] = uuid.uuid4().hex
    return response


def unauthorized(authorization: str | None) -> None:
    if not shop.authorized(authorization):
        raise HTTPException(401, "unauthorized")


@app.get("/health")
async def health() -> Response:
    return Response("ok", media_type="text/plain; charset=utf-8")


@app.get("/products")
async def products(q: Annotated[ProductQuery, Query()]) -> dict[str, object]:
    await shop.database(shop.READ_LATENCY)
    return shop.list_products(q.category, q.min_price, q.max_price, q.sort, q.page, q.per_page)


@app.get("/products/{product_id}")
async def product(product_id: int) -> dict[str, object]:
    await shop.database(shop.READ_LATENCY)
    try:
        return shop.get_product(product_id)
    except shop.ShopError as exc:
        raise HTTPException(exc.status, exc.code) from exc


@app.post("/orders", status_code=201)
async def create_order(
    body: NewOrder, authorization: Annotated[str | None, Header()] = None
) -> dict[str, object]:
    unauthorized(authorization)
    await shop.database(shop.WRITE_LATENCY)
    try:
        return shop.create_order(
            body.customer_id, [(line.product_id, line.quantity) for line in body.lines], body.coupon
        )
    except shop.ShopError as exc:
        raise HTTPException(exc.status, exc.code) from exc


@app.get("/orders/{order_id}")
async def order(
    order_id: int, authorization: Annotated[str | None, Header()] = None
) -> dict[str, object]:
    unauthorized(authorization)
    await shop.database(shop.READ_LATENCY)
    try:
        return shop.get_order(order_id)
    except shop.ShopError as exc:
        raise HTTPException(exc.status, exc.code) from exc
