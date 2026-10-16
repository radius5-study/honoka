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
