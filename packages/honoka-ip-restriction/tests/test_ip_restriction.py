"""ip_restriction."""

from honoka_ip_restriction import ip_restriction

from honoka import App, Context, Middleware, Response


def status_of(mw: Middleware) -> tuple[int, str]:
    app = App()
    app.use("*", mw)

    def index(c: Context) -> Response:
        return c.text("ok")

    app.get("/", index)

    with app.test() as client:
        resp = client.get("/")
    return resp.status, resp.text


def test_no_rules_lets_everyone_in() -> None:
    assert status_of(ip_restriction()) == (200, "ok")


def test_an_address_in_the_allow_list_passes_and_others_are_rejected() -> None:
    assert status_of(ip_restriction(allow_list=["127.0.0.0/8"]))[0] == 200
    assert status_of(ip_restriction(allow_list=["10.0.0.0/8", "::1"]))[0] == 403


def test_a_single_address_and_the_wildcard_are_rules() -> None:
    assert status_of(ip_restriction(allow_list=["127.0.0.1"]))[0] == 200
    assert status_of(ip_restriction(allow_list=["*"]))[0] == 200
    assert status_of(ip_restriction(deny_list=["*"]))[0] == 403


def test_a_deny_list_only_rejects_matches() -> None:
    assert status_of(ip_restriction(deny_list=["127.0.0.0/24"]))[0] == 403
    assert status_of(ip_restriction(deny_list=["192.168.0.0/16", "fe80::/10"]))[0] == 200


def test_the_deny_list_wins_over_the_allow_list() -> None:
    assert status_of(ip_restriction(allow_list=["127.0.0.0/8"], deny_list=["127.0.0.1"]))[0] == 403


def test_the_default_rejection_is_a_403_error_body() -> None:
    app = App()
    app.use("*", ip_restriction(deny_list=["*"]))
    with app.test() as client:
        resp = client.get("/")
    assert resp.status == 403
    assert resp.json() == {"error": "forbidden", "detail": "ip address is not allowed"}


def test_a_custom_blocked_response_receives_the_address() -> None:
    def blocked(addr: str | None, c: Context) -> Response:
        return c.text(f"no {addr}", 418)

    assert status_of(ip_restriction(deny_list=["127.0.0.1"], on_blocked=blocked)) == (
        418,
        "no 127.0.0.1",
    )


def test_an_async_blocked_hook_and_a_custom_ip_getter() -> None:
    async def blocked(addr: str | None, c: Context) -> Response:
        return c.text(f"no {addr}", 401)

    def forwarded(c: Context) -> str | None:
        return c.req.header("x-real-ip")

    assert status_of(
        ip_restriction(allow_list=["10.0.0.0/8"], get_ip=forwarded, on_blocked=blocked)
    ) == (401, "no None")


def test_an_ipv6_range_does_not_match_an_ipv4_peer() -> None:
    assert status_of(ip_restriction(allow_list=["::/0"]))[0] == 403


def test_an_address_that_cannot_be_determined_is_blocked_even_with_only_a_deny_list() -> None:
    app = App()
    app.use("*", ip_restriction(deny_list=["10.0.0.0/8"], get_ip=lambda c: None))

    def home(c: Context) -> Response:
        return c.text("in")

    app.get("/", home)

    with app.test() as client:
        assert client.get("/").status == 403


def test_an_ipv4_mapped_ipv6_peer_is_matched_by_both_forms() -> None:
    app = App()
    app.use(
        "*",
        ip_restriction(deny_list=["10.0.0.0/8"], get_ip=lambda c: "::ffff:10.1.2.3"),
    )

    def home(c: Context) -> Response:
        return c.text("in")

    app.get("/", home)

    mapped = App()
    mapped.use(
        "*",
        ip_restriction(deny_list=["::ffff:10.0.0.0/104"], get_ip=lambda c: "::ffff:10.1.2.3"),
    )
    mapped.get("/", home)

    with app.test() as client, mapped.test() as other:
        assert client.get("/").status == 403
        assert other.get("/").status == 403
