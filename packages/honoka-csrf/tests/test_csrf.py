"""csrf."""

from collections.abc import Sequence

from honoka_csrf import csrf

from honoka import App, Context, Response

FORM = {"content-type": "application/x-www-form-urlencoded"}


def csrf_app(origins: Sequence[str] = ()) -> App:
    app = App()
    app.use("*", csrf(origins=origins))

    def transfer(c: Context) -> Response:
        return c.text("done")

    app.post("/transfer", transfer)

    def page(c: Context) -> Response:
        return c.text("page")

    app.get("/page", page)

    return app


def test_a_form_post_with_no_origin_information_is_rejected() -> None:
    with csrf_app().test() as client:
        resp = client.post("/transfer", headers=FORM)
    assert (resp.status, resp.json()) == (403, {"error": "csrf_rejected"})


def test_a_form_post_from_the_same_origin_is_accepted() -> None:
    with csrf_app().test() as client:
        resp = client.post(
            "/transfer", headers={**FORM, "host": "app.test", "origin": "https://app.test"}
        )
    assert resp.text == "done"


def test_a_form_post_from_another_origin_is_rejected_unless_allowed() -> None:
    cross = {**FORM, "host": "app.test", "origin": "https://other.test"}
    with csrf_app().test() as client:
        assert client.post("/transfer", headers=cross).status == 403
    with csrf_app(origins=["https://other.test"]).test() as client:
        assert client.post("/transfer", headers=cross).status == 200


def test_sec_fetch_site_decides_when_the_browser_sends_it() -> None:
    with csrf_app().test() as client:
        same = client.post("/transfer", headers={**FORM, "sec-fetch-site": "same-origin"})
        cross = client.post("/transfer", headers={**FORM, "sec-fetch-site": "cross-site"})
    assert (same.status, cross.status) == (200, 403)


def test_json_requests_and_safe_methods_are_not_checked() -> None:
    with csrf_app().test() as client:
        assert client.post("/transfer", json={"a": 1}).status == 200
        assert client.get("/page").status == 200


def test_a_post_with_no_content_type_is_checked_like_a_form() -> None:
    """A page can send a typeless POST (a no-cors fetch with a Blob) without a preflight, and a
    handler may parse the body as JSON whatever its type says, so it counts as text/plain."""
    cross = {"host": "app.test", "origin": "https://evil.test"}
    with csrf_app().test() as client:
        assert client.post("/transfer", headers=cross).status == 403
        assert client.post("/transfer").status == 403  # no origin information at all
        same = client.post("/transfer", headers={"host": "app.test", "origin": "https://app.test"})
        assert same.status == 200
