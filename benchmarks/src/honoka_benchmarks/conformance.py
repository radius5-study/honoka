"""Before any load, check that an app answers the contract exactly like the reference logic."""

import json
import urllib.error
import urllib.request

from honoka_benchmarks import shop
from honoka_benchmarks.mix import ORIGIN

ORDER = {
    "customer_id": 7,
    "lines": [{"product_id": 3, "quantity": 2}, {"product_id": 9, "quantity": 1}],
    "coupon": "SAVE10",
}
AUTH = {"authorization": shop.AUTH_HEADER}


def _call(
    base: str, method: str, path: str, *, body: object = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    data = None if body is None else json.dumps(body).encode()
    all_headers = {"origin": ORIGIN, **(headers or {})}
    if data is not None:
        all_headers["content-type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=all_headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return (
                int(resp.status),
                {k.lower(): v for k, v in resp.headers.items()},
                bytes(resp.read()),
            )
    except urllib.error.HTTPError as exc:
        return int(exc.code), {k.lower(): v for k, v in exc.headers.items()}, bytes(exc.read())


def problems(base: str) -> list[str]:
    found: list[str] = []

    def expect(label: str, ok: bool, detail: object = "") -> None:
        if not ok:
            found.append(f"{label} {detail}".strip())

    status, headers, body = _call(base, "GET", "/health")
    expect("GET /health", status == 200 and body == b"ok", (status, body[:60]))
    expect("CORS header", headers.get("access-control-allow-origin") == ORIGIN, headers)
    expect("request id header", len(headers.get("x-request-id", "")) >= 16, headers)

    status, _, body = _call(base, "GET", "/products?category=books&sort=-price&page=2&per_page=3")
    expected = shop.list_products("books", None, None, "-price", 2, 3)
    expect("GET /products", status == 200 and json.loads(body) == expected, (status, body[:120]))

    status, _, body = _call(base, "GET", "/products/42")
    expect("GET /products/42", status == 200 and json.loads(body) == shop.get_product(42), status)
    status, _, _ = _call(base, "GET", "/products/999999")
    expect("GET /products/999999 is 404", status == 404, status)
    status, _, _ = _call(base, "GET", "/products?per_page=1000")
    expect("per_page=1000 is a client error", status in (400, 422), status)

    status, _, _ = _call(base, "POST", "/orders", body=ORDER)
    expect("POST /orders without a token is 401", status == 401, status)
    status, _, body = _call(base, "POST", "/orders", body=ORDER, headers=AUTH)
    created = shop.create_order(7, [(3, 2), (9, 1)], "SAVE10")
    expect("POST /orders", status == 201 and json.loads(body) == created, (status, body[:160]))
    bad = {"customer_id": 7, "lines": [{"product_id": 3, "quantity": 0}]}
    status, _, _ = _call(base, "POST", "/orders", body=bad, headers=AUTH)
    expect("POST /orders with quantity 0 is a client error", status in (400, 422), status)

    status, _, _ = _call(base, "GET", "/orders/7")
    expect("GET /orders/7 without a token is 401", status == 401, status)
    status, _, body = _call(base, "GET", "/orders/7", headers=AUTH)
    expect("GET /orders/7", status == 200 and json.loads(body) == shop.get_order(7), status)
    return found
