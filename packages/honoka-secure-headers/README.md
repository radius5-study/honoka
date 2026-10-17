# honoka-secure-headers

Adds the usual security headers to every response, unless the handler already set them.

```python
from honoka_secure_headers import secure_headers

app.use("*", secure_headers())
app.use("*", secure_headers(x_frame_options="DENY", referrer_policy=None))
```

Defaults (`DEFAULTS`): `x-content-type-options: nosniff`, `x-frame-options: SAMEORIGIN`,
`referrer-policy: no-referrer`, `strict-transport-security: max-age=15552000; includeSubDomains`,
`cross-origin-opener-policy: same-origin`, `cross-origin-resource-policy: same-origin`.

Pass `name=value` (underscores for dashes) to replace one, or `name=None` to drop it. An unknown
name raises `TypeError` at startup, so a typo cannot silently disable a header.
