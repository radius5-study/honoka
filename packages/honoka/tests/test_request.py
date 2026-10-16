"""What a handler can read from the request, via the public test client."""

from honoka import App, Context, Response


def echo_app() -> App:
    app = App()

    @app.get("/query")
    def query(c: Context) -> Response:
        return c.json(
            {
                "q": c.req.query("q"),
                "missing": c.req.query("missing"),
                "empty": c.req.query("empty"),
                "tags": c.req.queries("tag"),
            }
        )

    @app.get("/cookie")
    def cookie(c: Context) -> Response:
        return c.json({"a": c.req.cookie("a"), "b": c.req.cookie("b"), "z": c.req.cookie("z")})

    @app.get("/accepts")
    def accepts(c: Context) -> Response:
        return c.json({"best": c.req.accepts(["application/json", "text/html"])})

    @app.get("/client")
    def client_(c: Context) -> Response:
        return c.text(c.req.client)

    @app.post("/echo")
    async def echo(c: Context) -> Response:
        first = await c.req.body()
        second = await c.req.body()
        return c.json({"same": first == second, "json": await c.req.json()})

    return app


def test_query_parameters_are_single_or_repeated() -> None:
    with echo_app().test() as client:
        resp = client.get("/query?q=hi%20there&tag=a&tag=b&empty=")
    assert resp.json() == {"q": "hi there", "missing": None, "empty": "", "tags": ["a", "b"]}


def test_cookies_are_parsed_from_the_cookie_header_with_quoted_values() -> None:
    with echo_app().test() as client:
        resp = client.get("/cookie", headers={"cookie": 'a=1; b="two words"; c=3'})
    assert resp.json() == {"a": "1", "b": "two words", "z": None}


def test_accepts_picks_the_offer_the_client_ranks_highest() -> None:
    cases = {
        "text/html, application/json;q=0.9": "text/html",
        "application/*": "application/json",
        "*/*": "application/json",
        "text/html;q=0": None,
        "image/png": None,
    }
    with echo_app().test() as client:
        for accept, expected in cases.items():
            assert client.get("/accepts", headers={"accept": accept}).json() == {"best": expected}


def test_a_request_without_an_accept_header_takes_the_first_offer() -> None:
    with echo_app().test() as client:
        assert client.get("/accepts").json() == {"best": "application/json"}


def test_the_client_address_comes_from_the_connection() -> None:
    with echo_app().test() as client:
        assert client.get("/client").text == "127.0.0.1:50000"


def test_the_body_can_be_read_more_than_once_and_parsed_as_json() -> None:
    with echo_app().test() as client:
        resp = client.post("/echo", json={"a": 1})
    assert resp.json() == {"same": True, "json": {"a": 1}}
