"""Helpers the tests share."""

from collections.abc import Callable

from honoka.testing import ClientResponse

SECRET = "test-secret-" + "x" * 32

type SignIn = Callable[[str], dict[str, str]]


def body(resp: ClientResponse) -> dict[str, object]:
    """The response body, which these tests expect to be a JSON object."""
    data = resp.json()
    assert isinstance(data, dict), data
    return {str(key): value for key, value in data.items()}


def rows(resp: ClientResponse) -> list[dict[str, object]]:
    """The response body, which these tests expect to be a JSON array of objects."""
    data = resp.json()
    assert isinstance(data, list), data
    return [{str(k): v for k, v in item.items()} for item in data if isinstance(item, dict)]
