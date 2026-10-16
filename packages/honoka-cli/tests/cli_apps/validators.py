"""A stand-in for a validator middleware: the CLI only relies on its `schemas` attribute."""

from honoka import Context, Next, Response


class FakeValidator:
    def __init__(self, **schemas: object) -> None:
        self.schemas = schemas

    async def __call__(self, c: Context, call_next: Next) -> Response:
        return await call_next()
