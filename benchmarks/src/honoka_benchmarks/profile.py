"""Profile one app in-process, with no network: stable enough to see what a request costs.

uv run python -m honoka_benchmarks.profile honoka --requests 30000
"""

import argparse
import asyncio
import cProfile
import importlib
import pstats
import time
from collections.abc import MutableMapping

from honoka_benchmarks.frameworks import FRAMEWORKS
from honoka_benchmarks.mix import build_pool

type Request = tuple[str, str, str, list[tuple[bytes, bytes]], bytes]


async def _send(message: MutableMapping[str, object]) -> None:
    pass


def parse(raw: bytes) -> Request:
    head, _, body = raw.partition(b"\r\n\r\n")
    request_line, *header_lines = head.decode().split("\r\n")
    method, target, _ = request_line.split(" ")
    path, _, query = target.partition("?")
    headers: list[tuple[bytes, bytes]] = []
    for line in header_lines:
        name, _, value = line.partition(": ")
        headers.append((name.lower().encode(), value.encode()))
    return method, path, query, headers, body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("framework", choices=[f.name for f in FRAMEWORKS if f.interface == "asgi"])
    parser.add_argument("--requests", type=int, default=30000)
    parser.add_argument("--scenario", default="mixed")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--no-profile", action="store_true")
    args = parser.parse_args()

    fw = next(f for f in FRAMEWORKS if f.name == args.framework)
    app = importlib.import_module(fw.module).app
    pool = [parse(p.raw) for p in build_pool(args.scenario, "h:1", 2000, 1)]

    async def drive(count: int) -> None:
        for i in range(count):
            method, path, query, headers, body = pool[i % len(pool)]
            scope = {
                "type": "http",
                "method": method,
                "path": path,
                "query_string": query.encode(),
                "headers": headers,
                "client": ("127.0.0.1", 1),
            }

            async def receive(body: bytes = body) -> MutableMapping[str, object]:
                return {"type": "http.request", "body": body, "more_body": False}

            await app(scope, receive, _send)

    from honoka_benchmarks import shop

    shop.READ_LATENCY = shop.WRITE_LATENCY = 0.0  # measure the framework, not the sleeping

    asyncio.run(drive(2000))  # warm up
    samples = []
    for _ in range(args.repeat):
        start = time.process_time()
        asyncio.run(drive(args.requests))
        samples.append((time.process_time() - start) / args.requests * 1000)
    # The minimum is the least disturbed run; the machine only ever adds noise, never removes it.
    print(
        f"{args.framework}: {min(samples):.3f} ms CPU per request, best of {args.repeat} "
        f"(median {sorted(samples)[len(samples) // 2]:.3f}; no network, no sleeps)"
    )
    if args.no_profile:
        return
    profiler = cProfile.Profile()
    profiler.enable()
    asyncio.run(drive(args.requests // 3))
    profiler.disable()
    pstats.Stats(profiler).sort_stats("tottime").print_stats(args.top)


if __name__ == "__main__":
    main()
