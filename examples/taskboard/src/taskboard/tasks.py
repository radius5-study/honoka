"""Tasks below a project, plus the live event stream of that project."""

import asyncio
import time
from collections.abc import AsyncIterator

import msgspec
from honoka_jwt import JWT_PAYLOAD

from honoka import App, Context, HTTPError, Json, Next, Response, SSEEvent, Stream
from honoka_msgspec import validator
from taskboard import repo
from taskboard.deps import BUS, DB, PROJECT, SETTINGS, current_user_id
from taskboard.events import EventBus
from taskboard.models import (
    ErrorBody,
    Event,
    EventsQuery,
    Page,
    TaskIn,
    TaskOut,
    TaskPatch,
    TaskQuery,
)

NOT_FOUND = {404: (ErrorBody, "No such project or task")}


async def project_access(c: Context, call_next: Next) -> Response:
    """Let the request through only when the project is the signed-in user's. Someone else's
    project answers 404, not 403, so ids do not reveal what exists."""
    owner, project_id = current_user_id(c), c.req.param("project_id", int)
    project = await c.get(DB).run(lambda conn: repo.get_project(conn, owner, project_id))
    if project is None:
        raise HTTPError(404, "project_not_found")
    c.set(PROJECT, project)
    return await call_next()


async def list_tasks(c: Context) -> Json[Page[TaskOut]]:
    """Tasks of the project, filtered by status and a title search, one page at a time."""
    project, query = c.get(PROJECT), c.req.valid(TaskQuery)
    items, total = await c.get(DB).run(lambda conn: repo.list_tasks(conn, project.id, query))
    return c.json(Page(items=items, total=total, limit=query.limit, offset=query.offset))


async def create_task(c: Context) -> Json[TaskOut]:
    project, data = c.get(PROJECT), c.req.valid(TaskIn)
    task, event = await c.get(DB).run(lambda conn: repo.create_task(conn, project.id, data))
    c.get(BUS).publish(event)
    c.header("location", f"/projects/{project.id}/tasks/{task.id}")
    return c.json(task, 201)


async def get_task(c: Context) -> Json[TaskOut]:
    project, task_id = c.get(PROJECT), c.req.param("task_id", int)
    task = await c.get(DB).run(lambda conn: repo.get_task(conn, project.id, task_id))
    if task is None:
        raise HTTPError(404, "task_not_found")
    return c.json(task)


async def update_task(c: Context) -> Json[TaskOut]:
    """Change some fields. Send the `version` you read to refuse overwriting a newer change."""
    project, task_id, patch = c.get(PROJECT), c.req.param("task_id", int), c.req.valid(TaskPatch)
    try:
        updated = await c.get(DB).run(
            lambda conn: repo.update_task(conn, project.id, task_id, patch)
        )
    except repo.VersionConflict:
        raise HTTPError(409, "version_conflict", "the task was changed by someone else") from None
    if updated is None:
        raise HTTPError(404, "task_not_found")
    task, event = updated
    c.get(BUS).publish(event)
    return c.json(task)


async def delete_task(c: Context) -> Response:
    project, task_id = c.get(PROJECT), c.req.param("task_id", int)
    event = await c.get(DB).run(lambda conn: repo.delete_task(conn, project.id, task_id))
    if event is None:
        raise HTTPError(404, "task_not_found")
    c.get(BUS).publish(event)
    return c.body(b"", 204)


def _frame(event: Event) -> SSEEvent:
    return SSEEvent(data=msgspec.json.encode(event).decode(), event=event.kind, id=str(event.id))


async def _frames(
    c: Context, project_id: int, last: int, live: bool, bus: EventBus, until: float | None
) -> AsyncIterator[SSEEvent]:
    """Events of the project, ending when it is deleted or when `until` (the access token's
    expiry, in epoch seconds) passes: ownership was checked once, when the stream opened."""
    db, heartbeat = c.get(DB), c.get(SETTINGS).sse_heartbeat
    since = last
    # Subscribe before replaying, so an event committed in between is not lost; the id check
    # below drops the ones the replay already sent.
    with bus.subscribe(project_id) as queue:
        missed = await db.run(lambda conn: repo.events_since(conn, project_id, since))
        for event in missed:
            last = event.id
            yield _frame(event)
        while live:
            wait = heartbeat if until is None else min(heartbeat, until - time.time())
            if wait <= 0:
                return
            try:
                event = await asyncio.wait_for(queue.get(), wait)
            except TimeoutError:
                if until is not None and time.time() >= until:
                    return
                yield SSEEvent(data="{}", event="ping")  # keeps proxies from closing the stream
                continue
            if event is None:  # the project was deleted
                return
            if event.id > last:
                last = event.id
                yield _frame(event)


async def follow(c: Context) -> Stream:
    """Server-Sent Events for the project. Reconnect with `Last-Event-ID` to catch up on what
    was missed; `?live=false` replays (at most 500 events) and closes instead of waiting."""
    project, query = c.get(PROJECT), c.req.valid(EventsQuery)
    header = c.req.header("last-event-id")
    last = int(header) if header is not None and header.isdecimal() else query.since
    expires = c.get(JWT_PAYLOAD).get("exp")
    until = float(expires) if isinstance(expires, int | float) else None
    return c.sse(_frames(c, project.id, last, query.live, c.get(BUS), until))


def router() -> App:
    """Mounted at `/projects/{project_id}/tasks`."""
    app = App()
    app.get("/", project_access, validator(query=TaskQuery), list_tasks, responses=NOT_FOUND)
    app.post(
        "/",
        project_access,
        validator(json=TaskIn),
        create_task,
        status=201,
        responses=NOT_FOUND,
    )
    app.get("/{task_id:int}", project_access, get_task, responses=NOT_FOUND)
    app.patch(
        "/{task_id:int}",
        project_access,
        validator(json=TaskPatch),
        update_task,
        responses={**NOT_FOUND, 409: (ErrorBody, "The task changed since you read it")},
    )
    app.delete("/{task_id:int}", project_access, delete_task, status=204, responses=NOT_FOUND)
    return app
