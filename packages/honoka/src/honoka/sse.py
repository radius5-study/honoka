"""Server-Sent Events framing."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SSEEvent:
    data: str
    event: str | None = None
    id: str | None = None
    retry: int | None = None  # milliseconds the client waits before reconnecting


def format_event(event: SSEEvent) -> bytes:
    for field in (event.event, event.id):
        if field is not None and ("\n" in field or "\r" in field):
            raise ValueError("an SSE event name or id must not contain line breaks")
    lines: list[str] = []
    if event.event is not None:
        lines.append(f"event: {event.event}")
    if event.id is not None:
        lines.append(f"id: {event.id}")
    if event.retry is not None:
        lines.append(f"retry: {event.retry}")
    lines.extend(f"data: {line}" for line in event.data.splitlines() or [""])
    return ("\n".join(lines) + "\n\n").encode()
