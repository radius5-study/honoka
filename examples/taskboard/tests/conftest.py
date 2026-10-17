"""Shared fixtures. Every test gets its own app on its own in-memory database."""

from collections.abc import Generator

import pytest
from taskboard_support import SECRET, SignIn, body

from honoka import App
from honoka.testing import Client
from taskboard import Settings, create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(secret=SECRET, database=":memory:")


@pytest.fixture
def app(settings: Settings) -> App:
    return create_app(settings)


@pytest.fixture
def client(app: App) -> Generator[Client]:
    with app.test() as test_client:
        yield test_client


@pytest.fixture
def sign_in(client: Client) -> SignIn:
    """`sign_in("a@example.com")` registers the user and returns the Authorization header."""

    def sign_in(email: str) -> dict[str, str]:
        credentials = {"email": email, "password": "correct horse battery"}
        assert client.post("/auth/register", json=credentials).status == 201
        token = body(client.post("/auth/login", json=credentials))["access_token"]
        return {"authorization": f"Bearer {token}"}

    return sign_in
