"""Pre-built request pools for each load scenario, so the client spends no time generating."""

import json
import random
from dataclasses import dataclass

from honoka_benchmarks.shop import (
    AUTH_HEADER,
    CATEGORIES,
    CUSTOMER_COUNT,
    ORDER_COUNT,
    PRODUCT_COUNT,
)

ORIGIN = "https://app.example"
OK = frozenset(range(200, 300))
CLIENT_ERROR = frozenset({400, 401, 404, 422})
NOT_FOUND = frozenset({404})


@dataclass(frozen=True, slots=True)
class Prepared:
    raw: bytes
    allowed: frozenset[int]


def _request(
    method: str, path: str, host: str, *, body: object = None, auth: bool = False
) -> bytes:
    payload = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}",
        "Accept: application/json",
        f"Origin: {ORIGIN}",
    ]
    if auth:
        headers.append(f"Authorization: {AUTH_HEADER}")
    if body is not None:
        headers += ["Content-Type: application/json", f"Content-Length: {len(payload)}"]
    return ("\r\n".join(headers) + "\r\n\r\n").encode() + payload


def _list(rnd: random.Random, host: str) -> Prepared:
    query = f"category={rnd.choice(CATEGORIES)}&page={rnd.randint(1, 5)}&per_page=50"
    if rnd.random() < 0.3:
        query += f"&sort={rnd.choice(('price', '-price'))}&min_price=10&max_price=300"
    return Prepared(_request("GET", f"/products?{query}", host), OK)


def _detail(rnd: random.Random, host: str) -> Prepared:
    return Prepared(_request("GET", f"/products/{rnd.randint(1, PRODUCT_COUNT)}", host), OK)


def _create(rnd: random.Random, host: str) -> Prepared:
    lines = [
        {"product_id": rnd.randint(1, PRODUCT_COUNT), "quantity": rnd.randint(1, 5)}
        for _ in range(rnd.randint(1, 4))
    ]
    body: dict[str, object] = {"customer_id": rnd.randint(1, CUSTOMER_COUNT), "lines": lines}
    if rnd.random() < 0.3:
        body["coupon"] = "SAVE10"
    if rnd.random() < 0.3:
        body["note"] = "please leave at the door"
    return Prepared(_request("POST", "/orders", host, body=body, auth=True), OK)


def _order(rnd: random.Random, host: str) -> Prepared:
    return Prepared(_request("GET", f"/orders/{rnd.randint(1, ORDER_COUNT)}", host, auth=True), OK)


def _invalid(rnd: random.Random, host: str) -> Prepared:
    kind = rnd.randrange(4)
    if kind == 0:
        return Prepared(_request("GET", f"/products/{PRODUCT_COUNT + 1000}", host), NOT_FOUND)
    if kind == 1:
        body = {"customer_id": 1, "lines": [{"product_id": 1, "quantity": 1}]}
        return Prepared(_request("POST", "/orders", host, body=body), CLIENT_ERROR)  # no token
    if kind == 2:
        body = {"customer_id": 1, "lines": [{"product_id": 1, "quantity": 0}]}
        return Prepared(_request("POST", "/orders", host, body=body, auth=True), CLIENT_ERROR)
    return Prepared(_request("GET", "/products?per_page=1000", host), CLIENT_ERROR)


# (weight, builder): the realistic mix is read-heavy with some writes and some bad requests.
MIXED = ((35, _list), (35, _detail), (12, _create), (8, _order), (10, _invalid))
LIST_HEAVY = ((1, _list),)
WRITE_HEAVY = ((1, _create),)

SCENARIOS = {"mixed": MIXED, "list-heavy": LIST_HEAVY, "write-heavy": WRITE_HEAVY}


def build_pool(name: str, host: str, size: int, seed: int) -> list[Prepared]:
    rnd = random.Random(seed)
    weighted = SCENARIOS[name]
    builders = [b for _, b in weighted]
    weights = [w for w, _ in weighted]
    return [rnd.choices(builders, weights)[0](rnd, host) for _ in range(size)]
