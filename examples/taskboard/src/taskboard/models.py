"""The request and response shapes. msgspec checks them on the way in and encodes them out."""

from datetime import date, datetime
from typing import Annotated, Literal

import msgspec

type Status = Literal["todo", "doing", "done"]
type Email = Annotated[str, msgspec.Meta(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)]
type Name = Annotated[str, msgspec.Meta(min_length=1, max_length=100)]
type Title = Annotated[str, msgspec.Meta(min_length=1, max_length=200)]


class Credentials(msgspec.Struct):
    email: Email
    password: Annotated[str, msgspec.Meta(min_length=8, max_length=128)]


class UserOut(msgspec.Struct):
    id: int
    email: str


class TokenOut(msgspec.Struct):
    access_token: str
    expires_in: int
    token_type: str = "bearer"


class ProjectIn(msgspec.Struct):
    name: Name


class ProjectPatch(msgspec.Struct):
    name: Name | msgspec.UnsetType = msgspec.UNSET


class ProjectOut(msgspec.Struct):
    id: int
    name: str
    created_at: datetime


class TaskIn(msgspec.Struct):
    title: Title
    description: Annotated[str, msgspec.Meta(max_length=5000)] = ""
    status: Status = "todo"
    priority: Annotated[int, msgspec.Meta(ge=1, le=3)] = 2
    due: date | None = None


class TaskPatch(msgspec.Struct):
    """Only the fields that are sent change. `version` makes the update conditional: it fails
    with 409 when someone else changed the task since the client read it."""

    title: Title | msgspec.UnsetType = msgspec.UNSET
    description: Annotated[str, msgspec.Meta(max_length=5000)] | msgspec.UnsetType = msgspec.UNSET
    status: Status | msgspec.UnsetType = msgspec.UNSET
    priority: Annotated[int, msgspec.Meta(ge=1, le=3)] | msgspec.UnsetType = msgspec.UNSET
    due: date | None | msgspec.UnsetType = msgspec.UNSET
    version: int | None = None


class TaskOut(msgspec.Struct):
    id: int
    project_id: int
    title: str
    description: str
    status: Status
    priority: int
    due: date | None
    version: int
    created_at: datetime
    updated_at: datetime


class TaskQuery(msgspec.Struct):
    status: Status | None = None
    q: Annotated[str, msgspec.Meta(max_length=100)] | None = None
    limit: Annotated[int, msgspec.Meta(ge=1, le=100)] = 20
    offset: Annotated[int, msgspec.Meta(ge=0)] = 0


class Page[T](msgspec.Struct):
    items: list[T]
    total: int
    limit: int
    offset: int


class EventsQuery(msgspec.Struct):
    live: bool = True  # false: replay what was missed, then close
    since: Annotated[int, msgspec.Meta(ge=0)] = 0


class Event(msgspec.Struct):
    id: int
    project_id: int
    kind: str  # task.created, task.updated, task.deleted
    task_id: int
    created_at: datetime


class ErrorBody(msgspec.Struct):
    error: str
    detail: str | None = None
