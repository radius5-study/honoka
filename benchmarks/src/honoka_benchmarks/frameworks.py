from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Framework:
    name: str
    module: str
    interface: str  # "rsgi" or "asgi" (served by Granian), or "own" (starts its own server)
    package: str  # the distribution whose version is recorded
    baseline: str | None  # the raw baseline it is compared with


FRAMEWORKS = (
    Framework("raw-rsgi", "honoka_benchmarks.apps.raw_rsgi", "rsgi", "granian", None),
    Framework("raw-asgi", "honoka_benchmarks.apps.raw_asgi", "asgi", "granian", None),
    Framework("honoka", "honoka_benchmarks.apps.honoka_app", "rsgi", "honoka", "raw-rsgi"),
    Framework("starlette", "honoka_benchmarks.apps.starlette_app", "asgi", "starlette", "raw-asgi"),
    Framework("fastapi", "honoka_benchmarks.apps.fastapi_app", "asgi", "fastapi", "raw-asgi"),
    Framework("litestar", "honoka_benchmarks.apps.litestar_app", "asgi", "litestar", "raw-asgi"),
    Framework("falcon", "honoka_benchmarks.apps.falcon_app", "asgi", "falcon", "raw-asgi"),
    Framework(
        "blacksheep", "honoka_benchmarks.apps.blacksheep_app", "asgi", "blacksheep", "raw-asgi"
    ),
    Framework("robyn", "honoka_benchmarks.apps.robyn_app", "own", "robyn", None),
)
