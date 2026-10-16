"""HTTP errors and their machine-readable rendering (RFC 9.2)."""

import logging
from typing import override

from honoka.response import Json

logger = logging.getLogger("honoka")


class HTTPError(Exception):
    """Raise from a handler or middleware to answer with `status` and a structured body."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail
        self.headers: list[tuple[str, str]] = [] if headers is None else headers

    def body(self) -> dict[str, object]:
        body: dict[str, object] = {"error": self.code}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class NotFound(HTTPError):
    """Nothing answers this request. `not_found` registrations take over, then the built-in 404."""

    def __init__(self) -> None:
        super().__init__(404, "not_found")


class ValidationFailed(HTTPError):
    """A request that did not match its schema. Raised by validators such as honoka-msgspec."""

    def __init__(self, fields: list[dict[str, object]], schema: str | None = None) -> None:
        super().__init__(400, "validation_failed")
        self.fields = fields
        self.schema = schema

    @override
    def body(self) -> dict[str, object]:
        body: dict[str, object] = {"error": self.code, "fields": self.fields}
        if self.schema is not None:
            body["schema"] = self.schema
        return body


def default_error_response(exc: Exception) -> Json[dict[str, object]]:
    """The response honoka sends for `exc` unless the app installs its own `on_error`."""
    if isinstance(exc, HTTPError):
        return Json(exc.body(), exc.status, list(exc.headers))
    logger.error("unhandled error", exc_info=exc)
    return Json({"error": "internal_error"}, 500)
