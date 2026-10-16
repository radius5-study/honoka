"""The load generator parses the three ways an HTTP/1.1 response can end, and counts honestly."""

import asyncio
import threading
from collections.abc import Iterator

import pytest
from honoka_benchmarks.loadgen import read_response, run_load


def reader_with(data: bytes) -> asyncio.StreamReader:
    """Must be called inside a running event loop."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def parse(data: bytes) -> tuple[int, int, bool]:
    async def go() -> tuple[int, int, bool]:
        return await read_response(reader_with(data))

    return asyncio.run(go())


def test_a_content_length_body_is_consumed_exactly() -> None:
    async def go() -> tuple[int, int, bool]:
        reader = reader_with(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhelloHTTP/1.1")
        result = await read_response(reader)
        assert await reader.read() == b"HTTP/1.1"  # the next response is left untouched
        return result

    assert asyncio.run(go()) == (200, 5, False)


def test_a_chunked_body_is_decoded_to_its_total_size() -> None:
    raw = b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n"
    assert parse(raw) == (201, 5, False)


def test_no_content_and_connection_close_are_understood() -> None:
    assert parse(b"HTTP/1.1 204 No Content\r\n\r\n") == (204, 0, False)
    closing = b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\nok"
    assert parse(closing) == (200, 2, True)


@pytest.fixture
def server() -> Iterator[tuple[int, list[int]]]:
    """A tiny HTTP server that answers every request with the next status in `statuses`."""
    statuses = [200]
    started = threading.Event()
    ports: list[int] = []
    stops: list[asyncio.Event] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":")[1])
                if length:
                    await reader.readexactly(length)
                status = statuses[0]
                body = b"x" * 100
                writer.write(
                    f"HTTP/1.1 {status} X\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()

    async def serve() -> None:
        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        ports.append(int(srv.sockets[0].getsockname()[1]))
        stops.append(asyncio.Event())
        started.set()
        await stops[0].wait()
        srv.close()

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    started.wait(5)
    yield ports[0], statuses


def test_requests_are_counted_and_unexpected_statuses_are_reported(
    server: tuple[int, list[int]],
) -> None:
    port, statuses = server
    ok = run_load(
        "127.0.0.1", port, "list-heavy", connections=4, processes=1, warmup=0.2, seconds=1.0
    )
    assert ok.requests > 100
    assert ok.unexpected == 0
    assert ok.p50_ms <= ok.p99_ms <= ok.max_ms
    assert ok.mb_received == pytest.approx(ok.requests * 100 / 1_000_000)

    statuses[0] = 500
    bad = run_load(
        "127.0.0.1", port, "list-heavy", connections=4, processes=1, warmup=0.2, seconds=1.0
    )
    assert bad.unexpected == bad.requests > 0
