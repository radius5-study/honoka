Status: ready-for-agent
Type: task

# 04: csrf treats a missing Content-Type as text/plain (F3)

AC: a cross-origin POST with no Content-Type is `403 csrf_rejected`; a same-origin one passes.
