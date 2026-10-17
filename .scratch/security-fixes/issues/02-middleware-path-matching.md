Status: ready-for-agent
Type: task

# 02: middleware paths use the router's rules (F6, F9)

`path_matcher(pattern, strict)` in `honoka.router` answers "does this pattern cover this path" with
the routing rules. `app.use`, `on_error`, `not_found`, WebSocket and mount gating and
`honoka_combine.except_` use it; the static `_applicability` shortcut is removed. AC: `use` with
`{id}`, `{x:re:..}`, `a*` and `/a/*/c` patterns applies; a bad pattern raises at `use`;
`strict=False` covers `/admin/` with `use("/admin")`; a regex parameter that spans segments does
not drop later middleware.
