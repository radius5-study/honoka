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
        self._query: dict[str, list[str]] | None = None
        self._cookies: dict[str, str] | None = None
        self._body: bytes | None = None
        self._valid: dict[str, object] | None = None
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
        values = self._query_map().get(name)
        return None if values is None else values[0]

    def queries(self, name: str) -> list[str]:
        return list(self._query_map().get(name, ()))

    def _query_map(self) -> dict[str, list[str]]:
        """Every query parameter, grouped by name, parsed once per request."""
        if self._query is None:
            text = self._scope.query_string
            grouped: dict[str, list[str]] = {}
            if "%" not in text and "+" not in text:
                # Nothing to decode: splitting by hand is several times faster than urllib.
                for part in text.split("&"):
                    if part:
                        name, _, value = part.partition("=")
                        grouped.setdefault(name, []).append(value)
            else:
                for name, value in parse_qsl(text, keep_blank_values=True):
                    grouped.setdefault(name, []).append(value)
            self._query = grouped
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
        if self._valid is None:
            self._valid = {}
        self._valid[target] = value

    def valid[T](self, kind: type[T], target: str | None = None) -> T:
        """The validated object of type `kind`. Pass `target` if two targets share the type."""
        valid: dict[str, object] = {} if self._valid is None else self._valid
        if target is not None:
            if target not in valid:
                raise LookupError(f"this route did not validate {target!r}")
            return cast(T, valid[target])
        wanted = _runtime_type(kind)
        if len(valid) == 1:
            # One validated part is the common case: no need to look for ambiguity.
            only = next(iter(valid.values()))
            if isinstance(only, wanted):
                return cast(T, only)
        matches = [t for t, obj in valid.items() if isinstance(obj, wanted)]
        name = getattr(kind, "__name__", repr(kind))
        if not matches:
            raise LookupError(f"this route did not validate {name}")
        if len(matches) > 1:
            raise ValueError(f"{name} is ambiguous: validated for {matches}; pass target=")
        return cast(T, valid[matches[0]])


def _runtime_type(kind: object) -> type:
    """The class to `isinstance`-check against: `list` for `list[User]`, the class itself else."""
    if isinstance(kind, type):
        return kind
    origin = get_origin(kind)
    if isinstance(origin, type):
        return origin
    message = f"cannot validate against {kind!r}"
    raise TypeError(message)


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
