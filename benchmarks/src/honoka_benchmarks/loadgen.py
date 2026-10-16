"""A closed-loop HTTP/1.1 load generator: many keep-alive connections spread over processes.

Written in asyncio on purpose so the benchmarks need no external tool. A Python client tops out
far above what these realistic endpoints (database latency, 15 KB bodies) let a server reach, and
the harness reports client CPU so saturation of the generator itself is visible."""

import array
import asyncio
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context

from honoka_benchmarks.mix import Prepared, build_pool


@dataclass(frozen=True, slots=True)
class Result:
    requests: int
    unexpected: int
    errors: int
    seconds: float
    rps: float
    p50_ms: float
    p90_ms: float
    p99_ms: float
    p999_ms: float
    max_ms: float
    mb_received: float


async def read_response(reader: asyncio.StreamReader) -> tuple[int, int, bool]:
    """Read one response; return (status, body bytes, whether the server will close)."""
    status_line = await reader.readline()
    if not status_line:
        raise ConnectionResetError("connection closed before a response")
    status = int(status_line.split(b" ", 2)[1])
    length, chunked, close = None, False, False
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.partition(b":")
        key = name.strip().lower()
        if key == b"content-length":
            length = int(value)
        elif key == b"transfer-encoding" and b"chunked" in value.lower():
            chunked = True
        elif key == b"connection" and b"close" in value.lower():
            close = True
    received = 0
    if chunked:
        while True:
            size = int((await reader.readline()).split(b";")[0], 16)
            if size == 0:
                await reader.readline()
                break
            await reader.readexactly(size + 2)
            received += size
    elif length is not None:
        await reader.readexactly(length)
        received = length
    elif status >= 200 and status not in (204, 304):
        received = len(await reader.read())  # body runs to connection close
        close = True
    return status, received, close


async def _connection(
    host: str,
    port: int,
    pool: list[Prepared],
    seed: int,
    measure_from: float,
    stop_at: float,
    latencies: array.array[int],
    counters: list[int],
) -> None:
    import random

    rnd = random.Random(seed)
    reader, writer = await asyncio.open_connection(host, port)
    while (start := time.perf_counter()) < stop_at:
        prepared = pool[rnd.randrange(len(pool))]
        try:
            writer.write(prepared.raw)
            status, received, close = await read_response(reader)
        except (ConnectionError, asyncio.IncompleteReadError):
            if start >= measure_from:
                counters[2] += 1
            writer.close()
            reader, writer = await asyncio.open_connection(host, port)
            continue
        if start >= measure_from:
            latencies.append(int((time.perf_counter() - start) * 1_000_000))
            counters[0] += 1
            counters[3] += received
            if status not in prepared.allowed:
                counters[1] += 1
        if close:
            writer.close()
            reader, writer = await asyncio.open_connection(host, port)
    writer.close()


async def _client(
    host: str, port: int, scenario: str, connections: int, warmup: float, seconds: float, seed: int
) -> tuple[bytes, list[int]]:
    pool = build_pool(scenario, f"{host}:{port}", 4000, seed)
    latencies: array.array[int] = array.array("I")
    counters = [0, 0, 0, 0]  # requests, unexpected status, connection errors, bytes
    now = time.perf_counter()
    measure_from, stop_at = now + warmup, now + warmup + seconds
    await asyncio.gather(
        *(
            _connection(
                host, port, pool, seed * 1000 + i, measure_from, stop_at, latencies, counters
            )
            for i in range(connections)
        )
    )
    return latencies.tobytes(), counters


def _client_process(args: tuple[str, int, str, int, float, float, int]) -> tuple[bytes, list[int]]:
    return asyncio.run(_client(*args))


def _percentile(sorted_us: array.array[int], fraction: float) -> float:
    if not sorted_us:
        return 0.0
    index = min(len(sorted_us) - 1, int(len(sorted_us) * fraction))
    return sorted_us[index] / 1000


def run_load(
    host: str,
    port: int,
    scenario: str,
    *,
    connections: int,
    processes: int,
    warmup: float,
    seconds: float,
) -> Result:
    per_process = max(1, connections // processes)
    jobs = [(host, port, scenario, per_process, warmup, seconds, 1 + n) for n in range(processes)]
    with ProcessPoolExecutor(processes, mp_context=get_context("spawn")) as pool:
        parts = list(pool.map(_client_process, jobs))
    merged: array.array[int] = array.array("I")
    totals = [0, 0, 0, 0]
    for raw, counters in parts:
        chunk: array.array[int] = array.array("I")
        chunk.frombytes(raw)
        merged.extend(chunk)
        totals = [a + b for a, b in zip(totals, counters, strict=True)]
    ordered = array.array("I", sorted(merged))
    requests = totals[0]
    return Result(
        requests=requests,
        unexpected=totals[1],
        errors=totals[2],
        seconds=seconds,
        rps=requests / seconds,
        p50_ms=_percentile(ordered, 0.50),
        p90_ms=_percentile(ordered, 0.90),
        p99_ms=_percentile(ordered, 0.99),
        p999_ms=_percentile(ordered, 0.999),
        max_ms=ordered[-1] / 1000 if ordered else 0.0,
        mb_received=totals[3] / 1_000_000,
    )
