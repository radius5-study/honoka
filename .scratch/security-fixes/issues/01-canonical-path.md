Status: ready-for-agent
Type: task

# 01: canonical request path (F4, part of F9)

`c.req.path`, routing, middleware gating, fallbacks, WebSocket and mount matching all use one
canonical path: `//` and `.` segments dropped, `..` resolved and never above the root, a single
trailing slash kept. AC: an auth middleware on `/admin/*` covers `//admin/x`, `/./admin/x` and
`/a/../admin/x`; `serve_static` behind it answers 401; the routed path equals the gated path.
