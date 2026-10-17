# Security scan findings: fix at the root

Status: ready-for-agent

The Claude Security scan of `bc31f25` (9 findings, none above MEDIUM) shows three root causes. The
package is not on PyPI yet, so behavior may change instead of being worked around.

1. **The path is read in three different ways.** The router, the middleware gate and the file
   server each interpret `c.req.path` on their own, so `//admin/x`, `/./admin/x` or a regex
   parameter that spans segments can reach a handler without the middleware that guards it
   (F4, F6, F9). Decision: the core canonicalises the request path once (empty and `.` segments
   removed, `..` resolved and clamped at the root, one trailing slash kept), and a middleware
   pattern is matched by the router's own pattern rules, never by a second implementation. No
   static "this middleware cannot apply" analysis: the gate decides per request.
2. **`c.redirect` trusts its caller** with a string a browser may read as another origin
   (F1, F2). Decision: `c.redirect` refuses control characters and backslashes; `trim_trailing_slash`
   percent-encodes the path it redirects to.
3. **`csrf` read "no Content-Type" as "not a form"** (F3). Decision: no Content-Type is `text/plain`
   (Hono).

The taskboard example has three further findings (F5, F7, F8): project ids are reused, event
streams outlive their project and token, login and registration are unthrottled, and registration
reveals existing emails. Decisions: `AUTOINCREMENT`, close streams on project deletion and at the
token's expiry, an in-memory throttle ahead of the scrypt call, and the email enumeration is
documented as the trade-off of a service without email verification.

Refs: `CLAUDE-SECURITY-20261017-075638/CLAUDE-SECURITY-RESULTS.md` (local, ignored by git).
