"""Configuration, read once at startup and handed to `create_app`."""

import os
from dataclasses import dataclass
from typing import Self

DEFAULT_DATABASE = "taskboard.db"
DEFAULT_TOKEN_TTL = 3600


@dataclass(frozen=True, slots=True)
class Settings:
    secret: str  # signs the access tokens; keep it out of the repository
    database: str = DEFAULT_DATABASE
    token_ttl: int = DEFAULT_TOKEN_TTL  # seconds an access token stays valid
    cors_origins: tuple[str, ...] = ()
    max_body: int = 64 * 1024
    request_timeout: float = 10.0
    sse_heartbeat: float = 15.0  # seconds of silence before a live stream sends a ping
    login_attempts: int = 5  # failed logins per client and email before a wait
    register_attempts: int = 10  # registrations per client before a wait
    throttle_window: float = 60.0  # seconds the two limits above are counted over

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("the secret must be at least 32 characters")

    @classmethod
    def from_env(cls) -> Self:
        """`TASKBOARD_SECRET` is required; `TASKBOARD_DATABASE`, `TASKBOARD_TOKEN_TTL` and
        `TASKBOARD_CORS_ORIGINS` (comma separated) are optional."""
        env = os.environ
        secret = env.get("TASKBOARD_SECRET")
        if not secret:
            raise RuntimeError("set TASKBOARD_SECRET (at least 32 characters)")
        origins = tuple(o for o in env.get("TASKBOARD_CORS_ORIGINS", "").split(",") if o)
        return cls(
            secret=secret,
            database=env.get("TASKBOARD_DATABASE", DEFAULT_DATABASE),
            token_ttl=int(env.get("TASKBOARD_TOKEN_TTL", DEFAULT_TOKEN_TTL)),
            cors_origins=origins,
        )
