"""The shop API every benchmark app implements: shared data and business logic.

Frameworks only differ in routing, parsing, validation, middleware and serialization; the work
below (filtering, sorting, pricing, token checks) is identical, as is the simulated database
latency, so the comparison is about the framework."""

import asyncio
import hashlib
import hmac
import random
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, TypedDict

import msgspec

CATEGORIES = ("books", "games", "garden", "grocery", "home", "music", "sports", "toys")
_WORDS = ("alpha", "bravo", "cedar", "delta", "ember", "fjord", "grove", "harbor", "indigo", "jade")
_COLORS = ("red", "green", "blue", "black", "white", "amber")
_SECRET = b"benchmark-secret"
TOKEN = "bench-user." + hmac.new(_SECRET, b"bench-user", hashlib.sha256).hexdigest()
AUTH_HEADER = f"Bearer {TOKEN}"
PRODUCT_COUNT = 5000
CUSTOMER_COUNT = 2000
ORDER_COUNT = 100_000

# Simulated database round trips, in seconds.
READ_LATENCY = 0.002
WRITE_LATENCY = 0.003


class ProductAttributes(TypedDict):
    color: str
    weight_g: int
    rating: float
    reviews: int
    origin: str


class Product(TypedDict):
    id: int
    sku: str
    name: str
    category: str
    price: float
    stock: int
    tags: list[str]
    attributes: ProductAttributes
    created_at: str


class ShopError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class Line(msgspec.Struct):
    product_id: Annotated[int, msgspec.Meta(ge=1)]
    quantity: Annotated[int, msgspec.Meta(ge=1, le=10)]


class NewOrder(msgspec.Struct):
    customer_id: Annotated[int, msgspec.Meta(ge=1)]
    lines: Annotated[list[Line], msgspec.Meta(min_length=1, max_length=5)]
    note: Annotated[str, msgspec.Meta(max_length=200)] | None = None
    coupon: Annotated[str, msgspec.Meta(pattern=r"^[A-Z0-9]{4,12}$")] | None = None


class ProductQuery(msgspec.Struct):
    category: (
        Literal["books", "games", "garden", "grocery", "home", "music", "sports", "toys"] | None
    ) = None
    min_price: Annotated[float, msgspec.Meta(ge=0)] | None = None
    max_price: Annotated[float, msgspec.Meta(ge=0)] | None = None
    sort: Literal["name", "price", "-price"] = "name"
    page: Annotated[int, msgspec.Meta(ge=1)] = 1
    per_page: Annotated[int, msgspec.Meta(ge=1, le=100)] = 50


def _build_products() -> list[Product]:
    rnd = random.Random(42)
    return [
        {
            "id": i,
            "sku": f"SKU-{i:05d}",
            "name": f"Product {i} {rnd.choice(_WORDS)}",
            "category": CATEGORIES[i % len(CATEGORIES)],
            "price": round(rnd.uniform(1, 500), 2),
            "stock": rnd.randint(0, 500),
            "tags": [rnd.choice(_WORDS) for _ in range(3)],
            "attributes": {
                "color": rnd.choice(_COLORS),
                "weight_g": rnd.randint(50, 5000),
                "rating": round(rnd.uniform(1, 5), 1),
                "reviews": rnd.randint(0, 2000),
                "origin": rnd.choice(_WORDS),
            },
            "created_at": f"2025-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}T10:00:00Z",
        }
        for i in range(1, PRODUCT_COUNT + 1)
    ]


PRODUCTS = _build_products()
PRODUCT_BY_ID: dict[int, Product] = {p["id"]: p for p in PRODUCTS}
_BY_CATEGORY: dict[str, list[Product]] = {
    c: [p for p in PRODUCTS if p["category"] == c] for c in CATEGORIES
}


def parse_product_query(raw: Mapping[str, str]) -> ProductQuery:
    """For frameworks without built-in validation: msgspec does the same job they would."""
    return msgspec.convert(dict(raw), ProductQuery, strict=False)


def decode_order(body: bytes) -> NewOrder:
    return msgspec.json.decode(body, type=NewOrder)


def authorized(header: str | None) -> bool:
    """Verify `Bearer <user>.<hmac>` (an HMAC check per call, like a signed session token)."""
    if header is None or not header.startswith("Bearer "):
        return False
    user, _, signature = header[7:].partition(".")
    expected = hmac.new(_SECRET, user.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def list_products(
    category: str | None,
    min_price: float | None,
    max_price: float | None,
    sort: str,
    page: int,
    per_page: int,
) -> dict[str, object]:
    candidates = PRODUCTS if category is None else _BY_CATEGORY[category]
    low = min_price if min_price is not None else 0.0
    high = max_price if max_price is not None else float("inf")
    matching = [p for p in candidates if low <= p["price"] <= high]
    if sort == "name":
        matching.sort(key=lambda p: p["name"])
    else:
        matching.sort(key=lambda p: p["price"], reverse=sort == "-price")
    start = (page - 1) * per_page
    return {
        "items": matching[start : start + per_page],
        "page": page,
        "per_page": per_page,
        "total": len(matching),
    }


def list_products_for(q: ProductQuery) -> dict[str, object]:
    return list_products(q.category, q.min_price, q.max_price, q.sort, q.page, q.per_page)


def get_product(product_id: int) -> dict[str, object]:
    product = PRODUCT_BY_ID.get(product_id)
    if product is None:
        raise ShopError(404, "product_not_found")
    return dict(product)


def create_order(
    customer_id: int, lines: Sequence[tuple[int, int]], coupon: str | None
) -> dict[str, object]:
    if customer_id > CUSTOMER_COUNT:
        raise ShopError(404, "customer_not_found")
    out: list[dict[str, object]] = []
    total = 0.0
    for product_id, quantity in lines:
        product = PRODUCT_BY_ID.get(product_id)
        if product is None:
            raise ShopError(422, "unknown_product")
        subtotal = product["price"] * quantity
        total += subtotal
        out.append({"product_id": product_id, "quantity": quantity, "subtotal": round(subtotal, 2)})
    if coupon == "SAVE10":
        total *= 0.9
    digest = hashlib.sha256(f"{customer_id}:{total}".encode()).hexdigest()
    return {
        "id": int(digest[:8], 16) % ORDER_COUNT + 1,
        "customer_id": customer_id,
        "lines": out,
        "total": round(total, 2),
        "status": "created",
    }


def create_order_for(order: NewOrder) -> dict[str, object]:
    lines = [(line.product_id, line.quantity) for line in order.lines]
    return create_order(order.customer_id, lines, order.coupon)


def get_order(order_id: int) -> dict[str, object]:
    if not 1 <= order_id <= ORDER_COUNT:
        raise ShopError(404, "order_not_found")
    rnd = random.Random(order_id)
    lines = [
        {"product_id": rnd.randint(1, PRODUCT_COUNT), "quantity": rnd.randint(1, 5)}
        for _ in range(rnd.randint(1, 4))
    ]
    return {
        "id": order_id,
        "customer_id": rnd.randint(1, CUSTOMER_COUNT),
        "lines": lines,
        "status": rnd.choice(("created", "paid", "shipped")),
    }


async def database(seconds: float) -> None:
    await asyncio.sleep(seconds)
