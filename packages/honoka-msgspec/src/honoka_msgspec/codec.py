import msgspec


class MsgspecCodec:
    """Pass to `App(json_codec=MsgspecCodec())` so `c.json()` encodes Structs directly."""

    def encode(self, obj: object, /) -> bytes:
        return msgspec.json.encode(obj)

    def decode(self, data: bytes, /) -> object:
        decoded: object = msgspec.json.decode(data)
        return decoded
