import json
from datetime import UTC, datetime

from honoka_pretty_json import pretty_json

from honoka import App, Context, Middleware, Response

DATA = {"name": "ほのか", "tags": [1, 2]}
PRETTY = '{\n  "name": "ほのか",\n  "tags": [\n    1,\n    2\n  ]\n}'
COMPACT = '{"name":"ほのか","tags":[1,2]}'


def _app(mw: Middleware) -> App:
    app = App()
    app.use("*", mw)

    @app.get("/")
    def j(c: Context) -> Response:
        return c.json(DATA)

    @app.get("/t")
    def t(c: Context) -> Response:
        return c.text('{"a":1}')

    return app


def test_flag_pretty_prints_and_keeps_non_ascii() -> None:
    with _app(pretty_json()).test() as client:
        resp = client.get("/?pretty")
    assert resp.text == PRETTY
    assert resp.headers["content-type"] == "application/json"


def test_without_flag_is_compact() -> None:
    with _app(pretty_json()).test() as client:
        assert client.get("/").text == COMPACT


def test_space_query_and_force() -> None:
    with _app(pretty_json(space=4, query="p")).test() as client:
        assert client.get("/?p=1").text.splitlines()[1] == '    "name": "ほのか",'
        assert client.get("/?pretty").text == COMPACT
    with _app(pretty_json(force=True)).test() as client:
        assert client.get("/").text == PRETTY


def test_non_json_response_untouched() -> None:
    with _app(pretty_json()).test() as client:
        assert client.get("/t?pretty").text == '{"a":1}'


def test_a_custom_json_codec_keeps_working_when_the_response_is_indented() -> None:
    class DateCodec:
        def encode(self, obj: object) -> bytes:
            return json.dumps(obj, default=lambda o: o.isoformat()).encode()

        def decode(self, data: bytes) -> object:
            return json.loads(data)

    app = App(json_codec=DateCodec())
    app.use("*", pretty_json())

    @app.get("/")
    def home(c: Context) -> Response:
        return c.json({"at": datetime(2026, 10, 17, tzinfo=UTC)})

    with app.test() as client:
        resp = client.get("/?pretty")
    assert resp.status == 200
    assert resp.text == '{\n  "at": "2026-10-17T00:00:00+00:00"\n}'
