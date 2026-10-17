# taskboard

A task board API built on honoka. It is meant to be read: every honoka feature a real service
needs is used once, in the place where a real service would use it.

- accounts with scrypt-hashed passwords and JWT access tokens (`auth.py`)
- projects and tasks per user, where another user's data answers 404 (`projects.py`, `tasks.py`)
- validation with msgspec, partial updates, paging, filtering, optimistic concurrency (`models.py`)
- Server-Sent Events with replay through `Last-Event-ID` (`tasks.py`, `events.py`)
- SQLite from the standard library, run on worker threads (`db.py`, `repo.py`)
- request ids, access log, timing, security headers, CORS, gzip, ETag, body limit, timeout
  (`app.py`), and OpenAPI with a Scalar page at `/docs`

## Run

```sh
export TASKBOARD_SECRET="$(openssl rand -hex 32)"       # required, at least 32 characters
uv run granian --interface asgi --factory taskboard.app:create_app
```

`TASKBOARD_DATABASE` (default `taskboard.db`), `TASKBOARD_TOKEN_TTL` (seconds, default 3600) and
`TASKBOARD_CORS_ORIGINS` (comma separated, default none) are optional. Open
<http://127.0.0.1:8000/docs> for the API reference.

```sh
B=http://127.0.0.1:8000
curl -X POST $B/auth/register -H 'content-type: application/json' \
     -d '{"email": "ann@example.com", "password": "correct horse battery"}'
TOKEN=$(curl -s -X POST $B/auth/login -H 'content-type: application/json' \
     -d '{"email": "ann@example.com", "password": "correct horse battery"}' | jq -r .access_token)
curl -X POST $B/projects -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"name": "Launch"}'
curl -X POST $B/projects/1/tasks -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"title": "Write the docs", "priority": 1}'
curl "$B/projects/1/tasks?status=todo&q=docs" -H "authorization: Bearer $TOKEN"
curl -N $B/projects/1/events -H "authorization: Bearer $TOKEN"     # live; try a change elsewhere
```

## API

| | |
|---|---|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | accounts |
| `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}` | the signed-in user's projects |
| `GET/POST /projects/{id}/tasks` | list (`status`, `q`, `limit`, `offset`) and create |
| `GET/PATCH/DELETE /projects/{id}/tasks/{task_id}` | one task; send `version` in a PATCH to refuse overwriting a newer change (409) |
| `GET /projects/{id}/events` | SSE: `?live=false` replays and closes, `Last-Event-ID` resumes |
| `GET /health`, `GET /ready` | liveness and database check |

## Known trade-offs

- **Registration says whether an email is taken** (`409 email_taken`), so it can be used to test
  which addresses have accounts. Hiding it needs an email round trip (the same answer for every
  address, the outcome sent by mail), which a sample without a mail service cannot show. Login does
  not leak it: an unknown email takes the same time and answers the same way as a wrong password.
- **Password guessing and sign-up floods are slowed, not stopped.** `taskboard.throttle` counts in
  memory, per process: 5 failed logins per client and email, 10 registrations per client, each over
  a minute (`Settings`), answered `429` with `Retry-After` before any password is hashed. With
  several workers each counts for itself, and behind a reverse proxy every client is the proxy
  until you read a forwarded address from a proxy you trust. A shared store is the next step.
- **A stream ends** when its project is deleted and when the access token that opened it
  expires; it re-checks nothing else while it is open.

## Reading guide

- `app.py` is the whole assembly: the middleware order and why, the one place authentication is
  attached, route groups with `app.route`, `on_error`, `not_found`, docs.
- Handlers are plain functions of one `Context`, registered by calling `app.get(path, *middleware,
  handler)`. Per-route middleware (`validator(...)`, `project_access`) sits before the handler.
- `deps.py` holds the typed `Key`s handlers read with `c.get(KEY)`; nothing is a global.
- The database is the one piece of blocking code. `Database.run` moves each transaction to a
  worker thread, so the event loop only ever awaits.

## Test

```sh
uv run pytest examples
```

Tests drive the app through `app.test()` on an in-memory database, the way a client would.
