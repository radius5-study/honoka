Status: ready-for-agent
Type: task

# 03: c.redirect and trim_trailing_slash (F1, F2)

AC: `c.redirect` raises `ValueError` for any ASCII control character (tab included) and for a
backslash; `trim_trailing_slash` answers `/%5Cevil.test/` and `/%09/evil.test/` with a same-origin
`Location` (`/%5Cevil.test`), never `/\evil.test`.
