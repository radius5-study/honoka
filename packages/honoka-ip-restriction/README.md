# honoka-ip-restriction

Allow and deny lists by client address.

```python
from honoka_ip_restriction import ip_restriction

app.use("/admin/*", ip_restriction(allow_list=["192.168.0.0/16", "::1"]))
app.use("*", ip_restriction(deny_list=["203.0.113.7"]))
```

- Rules are addresses, CIDR ranges (IPv4 or IPv6) or `*`.
- The deny list is checked first and wins. Then an address in the allow list passes; otherwise it
  passes only when the allow list is empty.
- An address that cannot be determined is **blocked**, even when only a deny list is configured.
  `::ffff:1.2.3.4` is matched as both the IPv6 and the IPv4 address, so a v4 rule cannot be
  dodged through a mapped address.
- A blocked request answers `403`. `on_blocked(address, c)` replaces that with your own response.
- The address is the ASGI peer (`client_ip`). Behind a reverse proxy that is the proxy, so pass
  `get_ip=` a function that reads the forwarded header **only from a proxy you trust**:
  `X-Forwarded-For` is otherwise spoofable.
