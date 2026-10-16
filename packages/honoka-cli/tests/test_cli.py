"""`honoka routes` and `honoka check`, run in-process against sample apps."""

import pytest
from honoka_cli import main


def test_routes_lists_every_route_with_its_handler(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["routes", "cli_apps.good:app"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "GET   /health           health",
        "GET   /users/{user_id}  get_user",
        "POST  /users            create_user",
    ]


def test_check_passes_a_correct_app(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "cli_apps.good:app"]) == 0
    assert capsys.readouterr().out.strip() == "ok: 3 routes checked"


def test_check_reports_each_mistake_with_the_route_and_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", "cli_apps.bad:app"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert "✗ GET /users/{id}: path parameter 'id' is never read by unused_parameter" in out
    assert (
        "✗ GET /orders/{order_id}: c.req.param('ordr_id') is not a path parameter of this route"
        in out
    )
    assert "✗ POST /users: c.req.valid(UserIn) is called, but the route validates CreateUser" in out
    assert "! GET /legacy: no_return_annotation has no return annotation" in out
    assert out[-1] == "3 errors, 1 warning"


def test_an_app_that_cannot_be_imported_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", "cli_apps.nope:app"]) == 2
    assert "cannot import" in capsys.readouterr().err
