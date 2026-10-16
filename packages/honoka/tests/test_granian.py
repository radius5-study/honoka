"""The same App behind a real Granian RSGI server, to catch drift from the fake test server."""

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module", params=["rsgi", "asgi"])
def base_url(request: pytest.FixtureRequest) -> Iterator[str]:
    """The same app, served by real Granian through each interface."""
    port = free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "granian",
            "--interface",
            str(request.param),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "granian_app:app",
        ],
        cwd=HERE,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                httpx.get(f"{url}/health", timeout=1)
                break
            except httpx.TransportError:
                if time.monotonic() > deadline or server.poll() is not None:
                    raise
                time.sleep(0.1)
        yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_text_json_and_startup_published_values_work_over_real_http(base_url: str) -> None:
    assert httpx.get(f"{base_url}/health").text == "ok"
    resp = httpx.get(f"{base_url}/users/42")
    assert resp.json() == {"id": 42, "name": "pool"}
    assert resp.headers["content-type"] == "application/json"


def test_query_and_json_body_reach_the_handler(base_url: str) -> None:
    resp = httpx.post(f"{base_url}/echo?q=hi", json={"a": [1, 2]})
    assert resp.json() == {"query": "hi", "body": {"a": [1, 2]}}


def test_cookies_stream_and_errors_behave_like_the_fake_server(base_url: str) -> None:
    cookie = httpx.get(f"{base_url}/cookie", headers={"cookie": "in=yes"})
    assert cookie.text == "yes"
    assert "sid=abc" in cookie.headers["set-cookie"]

    assert httpx.get(f"{base_url}/stream").text == "abc"

    missing = httpx.get(f"{base_url}/nowhere")
    assert (missing.status_code, missing.headers["x-seen-status"]) == (404, "404")

    boom = httpx.get(f"{base_url}/boom")
    assert (boom.status_code, boom.json()) == (500, {"error": "internal_error"})

    wrong = httpx.delete(f"{base_url}/health")
    assert (wrong.status_code, wrong.headers["allow"]) == (405, "GET, HEAD")


def test_files_typed_parameters_and_wildcards_work_over_real_http(base_url: str) -> None:
    file = httpx.get(f"{base_url}/file")
    assert file.text == "hello file\n"
    assert file.headers["content-type"].startswith("text/plain")
    assert file.headers["content-disposition"] == "attachment; filename*=UTF-8''greeting.txt"

    assert httpx.head(f"{base_url}/file").content == b""

    assert httpx.get(f"{base_url}/typed/41").text == "42"
    assert httpx.get(f"{base_url}/typed/abc").status_code == 404
    assert httpx.get(f"{base_url}/wild/a/b").text == "/wild/a/b"
