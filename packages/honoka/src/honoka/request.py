import math
from collections.abc import Mapping, Sequence
from typing import cast, get_origin, overload
from urllib.parse import parse_qsl

from honoka._rsgi import Proto, Scope
from honoka.errors import HTTPError
from honoka.json import JsonCodec


class Request:
    __slots__ = (
        "_body",
        "_body_limit",
        "_codec",
        "_cookies",
        "_params",
        "_proto",
        "_query",
        "_scope",
        "_valid",
    )

    def __init__(
        self, scope: Scope, proto: Proto, params: Mapping[str, str], codec: JsonCodec
    ) -> None:
        self._scope = scope
        self._proto = proto
        self._params = params
        self._codec = codec
        self._query: list[tuple[str, str]] | None = None
        self._cookies: dict[str, str] | None = None
        self._body: bytes | None = None
        self._valid: dict[str, object] = {}
        self._body_limit: int | None = None

    @property
    def method(self) -> str:
        return self._scope.method

    @property
    def path(self) -> str:
        return self._scope.path

    def header(self, name: str) -> str | None:
        return self._scope.headers.get(name.lower())

    @overload
    def param(self, name: str) -> str: ...

    @overload
    def param[T: (str, int, float)](self, name: str, kind: type[T]) -> T: ...

    def param(self, name: str, kind: type[str | int | float] = str) -> str | int | float:
        """A path parameter, converted to `kind`. A value that does not convert is a 400."""
        try:
            raw = self._params[name]
        except KeyError:
            raise LookupError(f"route has no path parameter {name!r}") from None
        if kind is str:
            return raw
        try:
            if kind is int:
                if not (raw.isascii() and raw.lstrip("+-").isdigit()):
                    raise ValueError(raw)
                return int(raw)
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError(raw)
            return value
        except ValueError:
            raise HTTPError(
                400, "invalid_parameter", f"path parameter {name!r} must be {kind.__name__}"
            ) from None

    @property
    def query_string(self) -> str:
        return self._scope.query_string

    @property
    def params(self) -> Mapping[str, str]:
        """All path parameters, unconverted."""
        return self._params

    @property
    def client(self) -> str:
        return self._scope.client

    def query(self, name: str) -> str | None:
        """The first value of the query parameter `name`."""
        return next((v for k, v in self._query_items() if k == name), None)

    def queries(self, name: str) -> list[str]:
        return [v for k, v in self._query_items() if k == name]

    def _query_items(self) -> list[tuple[str, str]]:
        if self._query is None:
            self._query = parse_qsl(self._scope.query_string, keep_blank_values=True)
        return self._query

    def cookie(self, name: str) -> str | None:
        if self._cookies is None:
            self._cookies = _parse_cookies(self.header("cookie") or "")
        return self._cookies.get(name)

    def accepts(self, offers: Sequence[str]) -> str | None:
        """The best of `offers` for the Accept header; earlier offers win ties."""
        header = self.header("accept")
        if header is None:
            return offers[0] if offers else None
        ranges = _parse_accept(header)
        best, best_quality = None, 0.0
        for offer in offers:
            quality = _quality(offer, ranges)
            if quality > best_quality:
                best, best_quality = offer, quality
        return best

    def limit_body(self, max_bytes: int) -> None:
        """Make `body()` fail with 413 once more than `max_bytes` arrive."""
        self._body_limit = max_bytes

    async def body(self) -> bytes:
        if self._body is None:
            if self._body_limit is None:
                self._body = await self._proto()
            else:
                received = bytearray()
                async for chunk in self._proto:
                    received += chunk
                    if len(received) > self._body_limit:
                        raise HTTPError(413, "payload_too_large")
                self._body = bytes(received)
        return self._body

    async def json(self) -> object:
        """The body decoded as JSON, typed `object` so callers must narrow it."""
        return self._codec.decode(await self.body())

    def set_valid(self, target: str, value: object) -> None:
        """Store the object a validator produced for `target` (json, query, ...)."""
        self._valid[target] = value

    def valid[T](self, kind: type[T], target: str | None = None) -> T:
        """The validated object of type `kind`. Pass `target` if two targets share the type."""
        name = getattr(kind, "__name__", repr(kind))
        if target is not None:
            if target not in self._valid:
                raise LookupError(f"this route did not validate {target!r}")
            return cast(T, self._valid[target])
        wanted = get_origin(kind) or kind
        matches = [t for t, obj in self._valid.items() if isinstance(obj, wanted)]
        if not matches:
            raise LookupError(f"this route did not validate {name}")
        if len(matches) > 1:
            raise ValueError(f"{name} is ambiguous: validated for {matches}; pass target=")
        return cast(T, self._valid[matches[0]])


def _parse_cookies(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        cookies.setdefault(name, value)
    return cookies


def _parse_accept(header: str) -> list[tuple[str, float]]:
    ranges: list[tuple[str, float]] = []
    for part in header.split(","):
        mime, *params = (piece.strip() for piece in part.split(";"))
        quality = 1.0
        for param in params:
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        ranges.append((mime.lower(), quality))
    return ranges


def _quality(offer: str, ranges: list[tuple[str, float]]) -> float:
    """The quality of the most specific Accept range that matches `offer`."""
    best_specificity, quality = 0, 0.0
    for mime, q in ranges:
        if mime == offer:
            specificity = 3
        elif mime.endswith("/*") and mime != "*/*" and offer.startswith(mime[:-1]):
            specificity = 2
        elif mime == "*/*":
            specificity = 1
        else:
            continue
        if specificity > best_specificity:
            best_specificity, quality = specificity, q
    return quality
