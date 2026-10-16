import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal, cast, get_origin, overload
from urllib.parse import parse_qsl, quote

from honoka.asgi import HttpScope, Receive
from honoka.cookies import cookie_name, unsign
from honoka.errors import HTTPError
from honoka.forms import FormValue, Leaf, nest_dotted, parse_multipart
from honoka.json import JsonCodec


class Request:
    __slots__ = (
        "_body",
        "_body_limit",
        "_codec",
        "_cookies",
        "_headers",
        "_params",
        "_path",
        "_query",
        "_receive",
        "_scope",
        "_valid",
    )

    def __init__(
        self,
        scope: HttpScope,
        receive: Receive,
        params: Mapping[str, str],
        codec: JsonCodec,
        path: str,
    ) -> None:
        self._scope = scope
        self._path = path
        self._receive = receive
        self._headers: dict[str, str] | None = None
        self._params = params
        self._codec = codec
        self._query: dict[str, list[str]] | None = None
        self._cookies: dict[str, str] | None = None
        self._body: bytes | None = None
        self._valid: dict[str, object] | None = None
        self._body_limit: int | None = None

    @property
    def method(self) -> str:
        return self._scope["method"]

    @property
    def path(self) -> str:
        return self._path

    @overload
    def header(self, name: str) -> str | None: ...

    @overload
    def header(self) -> dict[str, str]: ...

    def header(self, name: str | None = None) -> str | None | dict[str, str]:
        """One request header (case-insensitive), or all of them with lowercase names."""
        if self._headers is None:
            self._headers = _index_headers(self._scope["headers"])
        return dict(self._headers) if name is None else self._headers.get(name.lower())

    @property
    def scope(self) -> HttpScope:
        """The raw ASGI scope, for adapters and middleware that need what honoka does not model."""
        return self._scope

    @property
    def url(self) -> str:
        """The full request URL, built from the scheme, the Host header and the path."""
        host = self.header("host")
        if host is None:
            server = self._scope.get("server")
            host = "localhost"
            if server:
                name = f"[{server[0]}]" if ":" in server[0] else server[0]
                host = f"{name}:{server[1]}" if server[1] else name
        scheme = self._scope.get("scheme", "http")
        root = self._scope.get("root_path", "")
        raw = self._scope.get("raw_path")
        # The path as it was sent, so `%3F` stays a path character and does not become a query.
        path = (
            raw.decode("latin-1")
            if isinstance(raw, bytes)
            else quote(self._path, safe="/:@!$&'()*+,;=-._~")
        )
        query = self.query_string
        return f"{scheme}://{host}{root}{path}{'?' + query if query else ''}"

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
        return self._scope["query_string"].decode("latin-1")

    @property
    def params(self) -> Mapping[str, str]:
        """All path parameters, unconverted."""
        return self._params

    @property
    def client(self) -> str:
        client = self._scope.get("client")
        return f"{client[0]}:{client[1]}" if client else ""

    @overload
    def query(self, name: str) -> str | None: ...

    @overload
    def query(self) -> dict[str, str]: ...

    def query(self, name: str | None = None) -> str | None | dict[str, str]:
        """The first value of the query parameter `name`, or every parameter's first value."""
        grouped = self._query_map()
        if name is None:
            return {key: values[0] for key, values in grouped.items()}
        values = grouped.get(name)
        return None if values is None else values[0]

    @overload
    def queries(self, name: str) -> list[str]: ...

    @overload
    def queries(self) -> dict[str, list[str]]: ...

    def queries(self, name: str | None = None) -> list[str] | dict[str, list[str]]:
        """Every value of the query parameter `name`, or all parameters with all their values."""
        grouped = self._query_map()
        if name is None:
            return {key: list(values) for key, values in grouped.items()}
        return list(grouped.get(name, ()))

    def _query_map(self) -> dict[str, list[str]]:
        """Every query parameter, grouped by name, parsed once per request."""
        if self._query is None:
            text = self.query_string
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

    @overload
    def cookie(
        self, name: str, *, prefix: Literal["secure", "host"] | None = None
    ) -> str | None: ...

    @overload
    def cookie(self) -> dict[str, str]: ...

    def cookie(
        self, name: str | None = None, *, prefix: Literal["secure", "host"] | None = None
    ) -> str | None | dict[str, str]:
        """A cookie by name (with `prefix`, the `__Secure-`/`__Host-` form of it), or all."""
        if self._cookies is None:
            self._cookies = _parse_cookies(self.header("cookie") or "")
        if name is None:
            return dict(self._cookies)
        return self._cookies.get(cookie_name(name, prefix))

    def signed_cookie(
        self, name: str, secret: str, *, prefix: Literal["secure", "host"] | None = None
    ) -> str | Literal[False] | None:
        """A cookie written by `Context.signed_cookie`: its value, `False` if the signature does
        not verify, `None` if the cookie is absent."""
        raw = self.cookie(name, prefix=prefix)
        if raw is None:
            return None
        value = unsign(raw, secret)
        return False if value is None else value

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
        """The whole body. A client that disconnects part-way leaves what had arrived."""
        if self._body is None:
            received = bytearray()
            limit = self._body_limit
            while True:
                message = await self._receive()
                if message["type"] != "http.request":  # http.disconnect
                    break
                received += cast(bytes, message.get("body", b""))
                if limit is not None and len(received) > limit:
                    raise HTTPError(413, "payload_too_large")
                if not message.get("more_body"):
                    break
            self._body = bytes(received)
        return self._body

    async def text(self) -> str:
        return (await self.body()).decode()

    async def parse_body(self, *, all: bool = False, dot: bool = False) -> dict[str, FormValue]:
        """A form body (`application/x-www-form-urlencoded` or `multipart/form-data`) as a dict.

        A repeated field keeps its last value, or all of them as a list with `all=True`. `dot=True`
        turns `a.b=1` into `{"a": {"b": 1}}`. Any other content type gives `{}`."""
        content_type = self.header("content-type") or ""
        body = await self.body()
        fields: list[tuple[str, Leaf]]
        if content_type.startswith("application/x-www-form-urlencoded"):
            fields = list(parse_qsl(body.decode(), keep_blank_values=True))
        elif content_type.startswith("multipart/form-data"):
            fields = parse_multipart(content_type, body)
        else:
            return {}
        parsed: dict[str, FormValue] = {}
        if all:
            grouped: dict[str, list[Leaf]] = {}
            for key, value in fields:
                grouped.setdefault(key, []).append(value)
            parsed.update(
                {k: v if len(v) > 1 or k.endswith("[]") else v[0] for k, v in grouped.items()}
            )
        else:
            parsed.update(fields)
        return nest_dotted(parsed) if dot else parsed

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


def _index_headers(raw: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    """Header fields by lowercase name. A repeated field is joined as RFC 9110 says (`cookie`,
    which HTTP/2 splits, with "; ")."""
    table: dict[str, str] = {}
    for name, value in raw:
        key = name.decode("latin-1").lower()
        text = value.decode("latin-1")
        if key in table:
            text = table[key] + ("; " if key == "cookie" else ", ") + text
        table[key] = text
    return table


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
