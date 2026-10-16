"""The shop API written by hand, once, for the two raw baselines (RSGI and ASGI)."""

import uuid
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl

import msgspec

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN

encode = msgspec.json.encode
order_decoder = msgspec.json.Decoder(shop.NewOrder)
JSON = "application/json"

Reply = tuple[int, bytes, str]


def error(status: int, code: str) -> Reply:
    return status, encode({"error": code}), JSON


async def handle(
    method: str,
    path: str,
    query_string: str,
    authorization: str | None,
    read_body: Callable[[], Awaitable[bytes]],
) -> Reply:
    try:
        if path == "/health" and method == "GET":
            return 200, b"ok", "text/plain; charset=utf-8"
        if path == "/products" and method == "GET":
            try:
                query = shop.parse_product_query(dict(parse_qsl(query_string)))
            except msgspec.ValidationError:
                return error(400, "validation_failed")
            await shop.database(shop.READ_LATENCY)
            return 200, encode(shop.list_products_for(query)), JSON
        if path.startswith("/products/") and method == "GET":
            await shop.database(shop.READ_LATENCY)
            return 200, encode(shop.get_product(int(path[10:]))), JSON
        if path == "/orders" and method == "POST":
            if not shop.authorized(authorization):
                return error(401, "unauthorized")
            try:
                order = order_decoder.decode(await read_body())
            except (msgspec.ValidationError, msgspec.DecodeError):
                return error(400, "validation_failed")
            await shop.database(shop.WRITE_LATENCY)
            return 201, encode(shop.create_order_for(order)), JSON
        if path.startswith("/orders/") and method == "GET":
            if not shop.authorized(authorization):
                return error(401, "unauthorized")
            await shop.database(shop.READ_LATENCY)
            return 200, encode(shop.get_order(int(path[8:]))), JSON
    except shop.ShopError as exc:
        return error(exc.status, exc.code)
    except ValueError:
        return error(400, "invalid_parameter")
    return error(404, "not_found")


def extra_headers(origin: str | None) -> list[tuple[str, str]]:
    headers = [("x-request-id", uuid.uuid4().hex)]
    if origin == ORIGIN:
        headers += [("access-control-allow-origin", origin), ("vary", "Origin")]
    return headers
