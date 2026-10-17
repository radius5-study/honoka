"""The event stream: replay for reconnecting clients, and live delivery to open ones."""

import asyncio
import json
from datetime import UTC, datetime

from taskboard_support import SignIn, ends_within, open_stream, sign_up

from honoka import App
from honoka.testing import Client, asgi_call
from taskboard.events import EventBus
from taskboard.models import Event


def frames(text: str) -> list[dict[str, str]]:
    """Parse an SSE body into {field: value} dicts, one per event."""
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        out.append(fields)
    return out


def make_history(client: Client, headers: dict[str, str]) -> str:
    client.post("/projects", json={"name": "P"}, headers=headers)
    client.post("/projects/1/tasks", json={"title": "a"}, headers=headers)
    client.patch("/projects/1/tasks/1", json={"status": "done"}, headers=headers)
    client.delete("/projects/1/tasks/1", headers=headers)
    return "/projects/1/events"


def test_a_late_client_replays_what_it_missed_in_order(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    events = make_history(client, ann)
    resp = client.get(f"{events}?live=false", headers=ann)
    assert resp.headers["content-type"] == "text/event-stream"
    got = frames(resp.text)
    assert [f["event"] for f in got] == ["task.created", "task.updated", "task.deleted"]
    assert [f["id"] for f in got] == ["1", "2", "3"]
    assert json.loads(got[0]["data"])["task_id"] == 1


def test_last_event_id_resumes_after_that_event(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    events = make_history(client, ann)
    resumed = client.get(f"{events}?live=false", headers={**ann, "last-event-id": "2"})
    assert [f["id"] for f in frames(resumed.text)] == ["3"]
    by_query = client.get(f"{events}?live=false&since=1", headers=ann)
    assert [f["id"] for f in frames(by_query.text)] == ["2", "3"]


def test_the_stream_belongs_to_the_projects_owner(client: Client, sign_in: SignIn) -> None:
    ann, bob = sign_in("ann@example.com"), sign_in("bob@example.com")
    events = make_history(client, ann)
    assert client.get(f"{events}?live=false", headers=bob).status == 404
    assert client.get(events).status == 401


def test_a_live_stream_delivers_changes_as_they_happen(app: App) -> None:
    async def scenario() -> list[dict[str, str]]:
        await app.start()
        try:
            token, auth = await sign_up(app)
            received = bytearray()
            stream = await open_stream(app, token, received)
            for title in ("one", "two"):
                content = json.dumps({"title": title}).encode()
                await asgi_call(app, "POST", "/projects/1/tasks", headers=auth, content=content)
            for _ in range(100):
                if received.count(b"\nid: ") == 2:  # one `id:` line per event
                    break
                await asyncio.sleep(0.02)
            await ends_within(stream, 0)  # the client went away
            return frames(received.decode())
        finally:
            await app.stop()

    got = asyncio.run(scenario())
    assert [f["id"] for f in got] == ["1", "2"]
    assert [json.loads(f["data"])["task_id"] for f in got] == [1, 2]


def event(event_id: int, project_id: int = 1) -> Event:
    return Event(
        id=event_id,
        project_id=project_id,
        kind="task.created",
        task_id=event_id,
        created_at=datetime.now(UTC),
    )


def test_the_bus_delivers_to_its_projects_subscribers_and_forgets_them_afterwards() -> None:
    bus = EventBus()
    with bus.subscribe(1) as mine, bus.subscribe(2) as other:
        bus.publish(event(1))
        assert mine.qsize() == 1 and other.qsize() == 0
    assert mine.qsize() == 1
    bus.publish(event(2))  # nobody is listening any more; must not fail, nor reach the old queue
    assert mine.qsize() == 1


def test_a_slow_subscriber_loses_its_oldest_events_instead_of_growing_forever() -> None:
    bus = EventBus()
    with bus.subscribe(1) as queue:
        for n in range(1, 151):
            bus.publish(event(n))
        assert queue.qsize() == 100
        oldest = queue.get_nowait()
        assert oldest is not None and oldest.id == 51
