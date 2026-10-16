import logging
from collections.abc import Callable, Sequence
from typing import Literal

from honoka import Context, Key, Middleware, Next, Response

LANGUAGE: Key[str] = Key("language")

Source = Literal["querystring", "cookie", "header", "path"]

_log = logging.getLogger("honoka_language")


def _parse_accept_language(header: str) -> list[str]:
    """Language tags by descending q (ties keep header order); q=0 and `*` are dropped."""
    ranked: list[tuple[float, str]] = []
    for part in header.split(","):
        tag, *params = (p.strip() for p in part.split(";"))
        if not tag or tag == "*":
            continue
        q = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if q > 0:
            ranked.append((q, tag))
    ranked.sort(key=lambda item: -item[0])
    return [tag for _, tag in ranked]


def language_detector(
    *,
    supported_languages: Sequence[str] = ("en",),
    fallback_language: str = "en",
    order: Sequence[Source] = ("querystring", "cookie", "header"),
    lookup_query_string: str = "lang",
    lookup_cookie: str = "language",
    lookup_from_header_key: str = "accept-language",
    lookup_from_path_index: int = 0,
    caches: bool = True,
    cookie_max_age: int = 365 * 24 * 60 * 60,
    cookie_path: str = "/",
    cookie_domain: str | None = None,
    cookie_secure: bool = True,
    cookie_http_only: bool = True,
    cookie_same_site: Literal["Lax", "Strict", "None"] = "Strict",
    ignore_case: bool = True,
    convert_detected_language: Callable[[str], str] | None = None,
    debug: bool = False,
) -> Middleware:
    """Detect the user's language and expose it as `LANGUAGE`. Sources are tried in `order`;
    the first that yields a supported language wins, else `fallback_language`. A region tag
    falls back to its base (`zh-Hant-CN` -> `zh-Hant` -> `zh`); an exact match wins first."""
    if fallback_language not in supported_languages:
        raise ValueError("fallback_language must be one of supported_languages")
    if lookup_from_path_index < 0:
        raise ValueError("lookup_from_path_index must be >= 0")

    supported = {(s.lower() if ignore_case else s): s for s in supported_languages}

    def match(candidate: str) -> str | None:
        if convert_detected_language is not None:
            candidate = convert_detected_language(candidate)
        if ignore_case:
            candidate = candidate.lower()
        while candidate:
            if candidate in supported:
                return supported[candidate]
            candidate, sep, _ = candidate.rpartition("-")
            if not sep:
                break
        return None

    def detect(c: Context) -> tuple[str, Source] | None:
        for source in order:
            candidates: list[str] = []
            if source == "querystring":
                value = c.req.query(lookup_query_string)
                candidates = [value] if value else []
            elif source == "cookie":
                value = c.req.cookie(lookup_cookie)
                candidates = [value] if value else []
            elif source == "header":
                candidates = _parse_accept_language(c.req.header(lookup_from_header_key) or "")
            else:
                segments = c.req.path.split("/")[1:]
                if lookup_from_path_index < len(segments):
                    candidates = [segments[lookup_from_path_index]]
            for candidate in candidates:
                found = match(candidate)
                if found is not None:
                    return found, source
        return None

    async def middleware(c: Context, call_next: Next) -> Response:
        detected = detect(c)
        language = fallback_language if detected is None else detected[0]
        if debug:
            _log.debug("language %s detected from %s", language, detected and detected[1])
        c.set(LANGUAGE, language)
        # Only a real detection is cached, so the fallback never pins a user to it.
        if caches and detected is not None and c.req.cookie(lookup_cookie) != language:
            c.cookie(
                lookup_cookie,
                language,
                max_age=cookie_max_age,
                path=cookie_path,
                domain=cookie_domain,
                secure=cookie_secure,
                http_only=cookie_http_only,
                same_site=cookie_same_site,
            )
        return await call_next()

    return middleware


__all__ = ["LANGUAGE", "language_detector"]
