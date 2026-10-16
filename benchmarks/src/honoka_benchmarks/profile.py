"""Profile one app in-process, with no network: stable enough to see what a request costs.

uv run python -m honoka_benchmarks.profile honoka --requests 30000
"""

import argparse
import asyncio
import cProfile
import importlib
import pstats
import time
from collections.abc import AsyncIterator, Iterable

from honoka_benchmarks.frameworks import FRAMEWORKS
from honoka_benchmarks.mix import build_pool


class _Headers:
    def __init__(self, items: list[tuple[str, str]]) -> None:
        self._items = items

    def get(self, key: str, default: str | None = None, /) -> str | None:
        return next((v for k, v in self._items if k == key), default)

    def items(self) -> Iterable[tuple[str, str]]:
        return list(self._items)


class _Scope:
    client = "127.0.0.1:1"

    def __init__(self, method: str, path: str, query: str, headers: list[tuple[str, str]]) -> None:
        self.method, self.path, self.query_string = method, path, query
        self.headers = _Headers(headers)


class _Proto:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __call__(self) -> bytes:
        return self._body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body

    def response_bytes(self, status: int, headers: list[tuple[str, str]], body: bytes, /) -> None:
        pass

    def response_empty(self, status: int, headers: list[tuple[str, str]], /) -> None:
        pass


def parse(raw: bytes) -> tuple[str, str, str, list[tuple[str, str]], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    request_line, *header_lines = head.decode().split("\r\n")
    method, target, _ = request_line.split(" ")
    path, _, query = target.partition("?")
    headers: list[tuple[str, str]] = []
    for line in header_lines:
        name, _, value = line.partition(": ")
        headers.append((name.lower(), value))
    return method, path, query, headers, body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("framework", choices=[f.name for f in FRAMEWORKS if f.interface == "rsgi"])
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
            await app.__rsgi__(_Scope(method, path, query, headers), _Proto(body))

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
