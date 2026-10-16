"""The validator middleware: decode and validate request parts into msgspec types."""

import re
import types
import typing
from collections.abc import Mapping
from urllib.parse import parse_qsl

import msgspec

from honoka import Context, HTTPError, Next, Response, ValidationFailed

_MESSAGE = re.compile(r"(?P<msg>.*?)(?: - at `(?P<at>[^`]*)`)?")
_MISSING = re.compile(r"Object missing required field `(?P<name>.+)`")
_PATH_TOKEN = re.compile(r"\.([^.\[\]]+)|\[(\d+)\]")
_SEQUENCES = (list, set, frozenset, tuple)
_FIELD_TARGETS = ("query", "param", "header", "cookie", "form")


def _wants_sequence(tp: object) -> bool:
    origin = typing.get_origin(tp)
    if origin in _SEQUENCES:
        return True
    if origin in (typing.Union, types.UnionType):
        return any(_wants_sequence(arg) for arg in typing.get_args(tp))
    return False


def _pretty(at: str) -> str:
    if at.startswith("$."):
        return at[2:]
    return at[1:] or "$"


def _lookup(raw: object, at: str) -> tuple[bool, object]:
    value = raw
    for name, index in _PATH_TOKEN.findall(at):
        if name and isinstance(value, dict) and name in value:
            value = value[name]
        elif index and isinstance(value, list) and int(index) < len(value):
            value = value[int(index)]
        else:
            return False, None
    return True, value


def _field_error(target: str, exc: msgspec.ValidationError, raw: object) -> dict[str, object]:
    match = _MESSAGE.fullmatch(str(exc))
    message = match["msg"] if match else str(exc)
    at = (match["at"] if match else None) or "$"
    missing = _MISSING.fullmatch(message)
    if missing:
        return {
            "in": target,
            "path": _pretty(f"{at}.{missing['name']}"),
            "expected": "required field",
        }
    expected = re.sub(r", got .*$", "", message.removeprefix("Expected ")).replace("`", "")
    entry: dict[str, object] = {"in": target, "path": _pretty(at), "expected": expected}
    found, value = _lookup(raw, at)
    if found:
        entry["received"] = value
    return entry


class Validator:
    """A middleware that validates the request parts it was given.

    The first failing target stops validation (msgspec reports one error at a time)."""

    def __init__(
        self,
        *,
        json: object = None,
        query: type[msgspec.Struct] | None = None,
        param: type[msgspec.Struct] | None = None,
        header: type[msgspec.Struct] | None = None,
        cookie: type[msgspec.Struct] | None = None,
        form: type[msgspec.Struct] | None = None,
        openapi_url: str = "/openapi.json",
    ) -> None:
        given: dict[str, object] = {
            "json": json,
            "query": query,
            "param": param,
            "header": header,
            "cookie": cookie,
            "form": form,
        }
        self.schemas: dict[str, object] = {t: s for t, s in given.items() if s is not None}
        for target in _FIELD_TARGETS:
            schema = self.schemas.get(target)
            if schema is not None and not (
                isinstance(schema, type) and issubclass(schema, msgspec.Struct)
            ):
                raise TypeError(f"{target} must be a msgspec.Struct subclass, got {schema!r}")
        self._openapi_url = openapi_url
        self._json = msgspec.json.Decoder(json) if json is not None else None

    async def __call__(self, c: Context, call_next: Next) -> Response:
        for target, schema in self.schemas.items():
            c.set_validated(target, await self._validate(c, target, schema))
        return await call_next()

    def _schema_ref(self, schema: object) -> str | None:
        name = getattr(schema, "__name__", None)
        return None if name is None else f"{self._openapi_url}#/components/schemas/{name}"

    async def _validate(self, c: Context, target: str, schema: object) -> object:
        if target == "json":
            return await self._validate_json(c, schema)
        raw = await self._raw(c, target, schema)
        try:
            return msgspec.convert(raw, schema, strict=False)  # type: ignore[arg-type]
        except msgspec.ValidationError as exc:
            raise ValidationFailed(
                [_field_error(target, exc, raw)], self._schema_ref(schema)
            ) from exc

    async def _validate_json(self, c: Context, schema: object) -> object:
        body = await c.req.body()
        assert self._json is not None
        try:
            return self._json.decode(body)
        except msgspec.ValidationError as exc:
            raw: object = msgspec.json.decode(body)
            raise ValidationFailed(
                [_field_error("json", exc, raw)], self._schema_ref(schema)
            ) from exc
        except msgspec.DecodeError as exc:
            raise HTTPError(400, "invalid_json", str(exc)) from exc

    async def _raw(self, c: Context, target: str, schema: object) -> Mapping[str, object]:
        assert isinstance(schema, type) and issubclass(schema, msgspec.Struct)
        fields = msgspec.structs.fields(schema)
        raw: dict[str, object] = {}
        items: list[tuple[str, str]] = []
        if target == "form":
            content_type = c.req.header("content-type") or ""
            if not content_type.startswith("application/x-www-form-urlencoded"):
                raise HTTPError(415, "unsupported_media_type", "expected a urlencoded form")
            items = parse_qsl((await c.req.body()).decode(), keep_blank_values=True)
        for field in fields:
            key = field.encode_name
            values: list[str]
            if target == "query":
                values = c.req.queries(key)
            elif target == "form":
                values = [v for k, v in items if k == key]
            elif target == "param":
                values = [c.req.params[key]] if key in c.req.params else []
            elif target == "header":
                header = c.req.header(key.replace("_", "-"))
                values = [] if header is None else [header]
            else:
                cookie = c.req.cookie(key)
                values = [] if cookie is None else [cookie]
            if values:
                raw[key] = values if _wants_sequence(field.type) else values[0]
        return raw


def validator(
    *,
    json: object = None,
    query: type[msgspec.Struct] | None = None,
    param: type[msgspec.Struct] | None = None,
    header: type[msgspec.Struct] | None = None,
    cookie: type[msgspec.Struct] | None = None,
    form: type[msgspec.Struct] | None = None,
    openapi_url: str = "/openapi.json",
) -> Validator:
    return Validator(
        json=json,
        query=query,
        param=param,
        header=header,
        cookie=cookie,
        form=form,
        openapi_url=openapi_url,
    )
