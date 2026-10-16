import math
from collections.abc import Mapping
from typing import overload

from honoka._rsgi import Proto, Scope
from honoka.errors import HTTPError


class Request:
    __slots__ = ("_params", "_proto", "_scope")

    def __init__(self, scope: Scope, proto: Proto, params: Mapping[str, str]) -> None:
        self._scope = scope
        self._proto = proto
        self._params = params

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
