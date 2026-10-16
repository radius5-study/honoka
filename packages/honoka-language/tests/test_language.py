import pytest
from honoka_language import LANGUAGE, language_detector

from honoka import App, Context, Middleware, Response


def _app(mw: Middleware) -> App:
    app = App()
    app.use("*", mw)

    @app.get("/")
    @app.get("/{lang}/page")
    def handler(c: Context) -> Response:
        return c.text(c.get(LANGUAGE))

    return app


SUPPORTED = ("en", "ja", "zh-Hant", "zh")


def _basic() -> App:
    return _app(language_detector(supported_languages=SUPPORTED, fallback_language="en"))


def test_fallback_without_signals_sets_no_cookie() -> None:
    with _basic().test() as client:
        resp = client.get("/")
    assert resp.text == "en"
    assert "set-cookie" not in resp.headers


def test_querystring_beats_cookie_beats_header_and_caches() -> None:
    with _basic().test() as client:
        resp = client.get("/?lang=ja", headers={"cookie": "language=zh", "accept-language": "en"})
        assert resp.text == "ja"
        cookie = resp.headers["set-cookie"]
        assert cookie.startswith("language=ja;")
        assert "Max-Age=31536000" in cookie
        assert "SameSite=Strict" in cookie
        assert "Secure" in cookie
        assert "HttpOnly" in cookie
        assert (
            client.get("/", headers={"cookie": "language=zh", "accept-language": "en"}).text == "zh"
        )


def test_cookie_already_matching_is_not_rewritten() -> None:
    with _basic().test() as client:
        resp = client.get("/", headers={"cookie": "language=ja"})
    assert resp.text == "ja"
    assert "set-cookie" not in resp.headers


def test_accept_language_q_values() -> None:
    with _basic().test() as client:
        resp = client.get("/", headers={"accept-language": "fr;q=0.9, ja;q=0.8, en;q=0.1, de;q=0"})
        assert resp.text == "ja"
        assert client.get("/", headers={"accept-language": "en;q=0.2, ja;q=0.7"}).text == "ja"
        assert client.get("/", headers={"accept-language": "ja;q=0, *"}).text == "en"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("zh-Hant-CN", "zh-Hant"), ("zh-CN", "zh"), ("ja-JP", "ja"), ("EN-us", "en"), ("fr", "en")],
)
def test_region_truncation_and_case(tag: str, expected: str) -> None:
    with _basic().test() as client:
        assert client.get("/", headers={"accept-language": tag}).text == expected


def test_ignore_case_off() -> None:
    app = _app(language_detector(supported_languages=("en", "ja"), ignore_case=False))
    with app.test() as client:
        assert client.get("/?lang=JA").text == "en"
        assert client.get("/?lang=ja").text == "ja"


def test_path_order_and_caches_off() -> None:
    app = _app(
        language_detector(supported_languages=("en", "ja"), order=("path", "header"), caches=False)
    )
    with app.test() as client:
        resp = client.get("/ja/page", headers={"accept-language": "en"})
        assert resp.text == "ja"
        assert "set-cookie" not in resp.headers
        assert client.get("/xx/page", headers={"accept-language": "en"}).text == "en"


def test_convert_detected_language() -> None:
    app = _app(
        language_detector(
            supported_languages=("en", "ja"),
            convert_detected_language=lambda s: s.replace("_", "-").split("-")[0],
        )
    )
    with app.test() as client:
        assert client.get("/?lang=ja_JP").text == "ja"


def test_invalid_config() -> None:
    with pytest.raises(ValueError, match="fallback_language"):
        language_detector(supported_languages=("ja",), fallback_language="en")
    with pytest.raises(ValueError, match="lookup_from_path_index"):
        language_detector(lookup_from_path_index=-1)
