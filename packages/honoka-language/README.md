# honoka-language

Detects the user's language and exposes it as `LANGUAGE`.

```python
from honoka_language import LANGUAGE, language_detector

app.use(
    "*",
    language_detector(
        supported_languages=["en", "ja", "zh-Hant"],
        fallback_language="en",
        order=["querystring", "cookie", "header"],
    ),
)

def greeting(c):
    return c.text({"en": "Hello", "ja": "こんにちは"}.get(c.get(LANGUAGE), "Hello"))
```

- Sources are tried in `order` (`querystring`, `cookie`, `header` for `Accept-Language`, `path`);
  the first that yields a supported language wins, else `fallback_language`. Both must be listed
  in `supported_languages`, or it raises `ValueError`.
- A region falls back to its base (`zh-Hant-CN` to `zh-Hant` to `zh`); an exact match wins first.
  `Accept-Language` is read by `q` value.
- With `caches=True` a detected language is stored in a cookie (`language`; strict, secure and
  http-only by default, one year), so the choice sticks. The fallback is never cached.
- Lookup names (`lookup_query_string`, `lookup_cookie`, `lookup_from_header_key`,
  `lookup_from_path_index`), the cookie attributes, `ignore_case` and
  `convert_detected_language` are options.
