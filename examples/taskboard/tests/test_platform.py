"""What every request gets: ids, headers, limits, error answers, CORS, and the API docs."""

from dataclasses import replace

from taskboard_support import SignIn, body

from honoka import App, Context, Response
from honoka.testing import Client
from taskboard import Settings, create_app


def test_every_response_carries_a_request_id_and_the_clients_own_is_kept(client: Client) -> None:
    first, second = client.get("/health"), client.get("/health")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    mine = client.get("/health", headers={"x-request-id": "trace-42"})
    assert mine.headers["x-request-id"] == "trace-42"


def test_responses_carry_the_security_headers_and_timing(client: Client) -> None:
    headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert "server-timing" in headers


def test_health_and_readiness_need_no_token(client: Client) -> None:
    assert (client.get("/health").status, body(client.get("/health"))) == (200, {"status": "ok"})
    assert body(client.get("/ready")) == {"status": "ready"}


def test_a_body_over_the_limit_is_refused_before_it_is_read(settings: Settings) -> None:
    with create_app(replace(settings, max_body=200)).test() as small:
        resp = small.post("/auth/register", content=b"x" * 500)
        assert (resp.status, body(resp)["error"]) == (413, "payload_too_large")


def test_unknown_paths_get_a_json_404(client: Client) -> None:
    resp = client.get("/nowhere")
    assert (resp.status, body(resp)) == (404, {"error": "not_found", "path": "/nowhere"})


def test_an_unexpected_failure_is_a_500_without_details_but_with_the_request_id(
    app: App,
) -> None:
    async def boom(c: Context) -> Response:
        raise RuntimeError("database password is hunter2")

    app.get("/boom", boom)
    with app.test() as client:
        resp = client.get("/boom", headers={"x-request-id": "trace-7"})
    assert (resp.status, body(resp)) == (500, {"error": "internal_error", "request_id": "trace-7"})
    assert "hunter2" not in resp.text


def test_cors_is_off_until_origins_are_configured(settings: Settings, client: Client) -> None:
    preflight = {"origin": "https://app.example", "access-control-request-method": "POST"}
    assert (
        "access-control-allow-origin" not in client.options("/projects", headers=preflight).headers
    )
    with create_app(replace(settings, cors_origins=("https://app.example",))).test() as open_:
        allowed = open_.options("/projects", headers=preflight)
        assert allowed.headers["access-control-allow-origin"] == "https://app.example"
        stranger = open_.options(
            "/projects", headers={**preflight, "origin": "https://evil.example"}
        )
        assert "access-control-allow-origin" not in stranger.headers


def test_large_listings_are_compressed_on_request(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    client.post("/projects", json={"name": "P"}, headers=ann)
    for n in range(40):
        client.post("/projects/1/tasks", json={"title": f"task number {n}"}, headers=ann)
    resp = client.get("/projects/1/tasks?limit=100", headers={**ann, "accept-encoding": "gzip"})
    assert resp.headers["content-encoding"] == "gzip"


def test_the_openapi_document_describes_the_routes_that_belong_to_the_api(client: Client) -> None:
    spec = body(client.get("/openapi.json"))
    paths = spec["paths"]
    assert isinstance(paths, dict)
    assert {"/auth/login", "/projects", "/projects/{project_id}/tasks/{task_id}"} <= set(paths)
    assert "/health" not in paths and "/docs" not in paths
    login = paths["/auth/login"]["post"]
    assert login["operationId"] == "login"
    assert login["requestBody"]["required"] is True
    assert client.get("/docs").status == 200
