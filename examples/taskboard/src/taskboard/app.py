"""Assembly: middleware order, route groups, error answers, docs."""

import logging

from honoka_body_limit import body_limit
from honoka_compress import compress
from honoka_cors import cors
from honoka_etag import etag
from honoka_jwt import jwt
from honoka_logger import logger
from honoka_request_id import REQUEST_ID, request_id
from honoka_secure_headers import secure_headers
from honoka_timeout import timeout
from honoka_timing import timing

from honoka import App, Context, Response
from honoka.errors import HTTPError
from honoka_msgspec import MsgspecCodec, validator
from honoka_openapi import docs
from taskboard import auth, projects, tasks
from taskboard.auth import ISSUER
from taskboard.db import Database
from taskboard.deps import BUS, DB, SETTINGS
from taskboard.events import EventBus
from taskboard.models import EventsQuery
from taskboard.settings import Settings

log = logging.getLogger("taskboard")


async def health(c: Context) -> Response:
    """The process is up."""
    return c.json({"status": "ok"})


async def ready(c: Context) -> Response:
    """The database answers."""
    await c.get(DB).run(lambda conn: conn.execute("SELECT 1").fetchone())
    return c.json({"status": "ready"})


async def fail(c: Context) -> Response:
    """Answer what went wrong. Errors the app raises on purpose keep their own answer; anything
    else is logged with the request id and shown to the client without details."""
    error = c.error
    if isinstance(error, HTTPError):
        return error.get_response()
    try:
        rid: str | None = c.get(REQUEST_ID)
    except LookupError:
        rid = None
    log.error("unhandled error rid=%s", rid, exc_info=error)
    return c.json({"error": "internal_error", "request_id": rid}, 500)


async def missing(c: Context) -> Response:
    return c.json({"error": "not_found", "path": c.req.path}, 404)


def create_app(settings: Settings | None = None) -> App:
    """Build the application. Without `settings`, read them from the environment."""
    settings = Settings.from_env() if settings is None else settings
    db = Database(settings.database)
    app = App(json_codec=MsgspecCodec())

    # Services handlers read with `c.get(KEY)`; the database lives as long as the server.
    app.provide(SETTINGS, settings)
    app.provide(DB, db)
    app.provide(BUS, EventBus())
    app.on_startup(db.open)
    app.on_shutdown(db.close)

    # Outermost first. The id is set before the access log reads it; compress sees the final
    # body, so the ETag is computed on the uncompressed bytes inside it.
    app.use("*", request_id())
    app.use("*", logger())
    app.use("*", timing())
    app.use("*", secure_headers())
    if settings.cors_origins:
        app.use(
            "*",
            cors(origins=settings.cors_origins, expose=["x-request-id", "etag", "location"]),
        )
    app.use("*", compress())
    app.use("*", etag())
    app.use("*", body_limit(settings.max_body))
    app.use("*", timeout(settings.request_timeout))

    # Authentication in one place: everything below these paths needs a valid access token.
    authenticate = jwt(secret=settings.secret, iss=ISSUER)
    app.use("/projects", authenticate)
    app.use("/projects/*", authenticate)

    app.get("/health", health, openapi=False)
    app.get("/ready", ready, openapi=False)
    app.route("/auth", auth.router(authenticate))
    app.route("/projects", projects.router())
    app.route("/projects/{project_id:int}/tasks", tasks.router())
    app.get(
        "/projects/{project_id:int}/events",
        tasks.project_access,
        validator(query=EventsQuery),
        tasks.follow,
    )

    app.on_error(fail)
    app.not_found(missing)
    docs(app, title="Taskboard API", version="1.0.0")
    return app
