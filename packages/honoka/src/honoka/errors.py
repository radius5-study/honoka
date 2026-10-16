"""HTTP errors and their machine-readable rendering (RFC 9.2)."""

import logging

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


def default_error_response(exc: Exception) -> Json[dict[str, object]]:
    """The response honoka sends for `exc` unless the app installs its own `on_error`."""
    if isinstance(exc, HTTPError):
        return Json(exc.body(), exc.status, list(exc.headers))
    logger.error("unhandled error", exc_info=exc)
    return Json({"error": "internal_error"}, 500)
