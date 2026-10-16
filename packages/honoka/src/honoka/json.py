"""JSON encoding for responses and request bodies. The core only needs the standard library;
packages such as honoka-msgspec provide faster codecs that also understand their own types."""

import json
from typing import Protocol


class JsonCodec(Protocol):
    def encode(self, obj: object, /) -> bytes: ...

    def decode(self, data: bytes, /) -> object: ...


class StdlibJson:
    """The default codec: compact output, UTF-8, no ASCII escaping."""

    def encode(self, obj: object, /) -> bytes:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()

    def decode(self, data: bytes, /) -> object:
        decoded: object = json.loads(data)
        return decoded


STDLIB_JSON = StdlibJson()
