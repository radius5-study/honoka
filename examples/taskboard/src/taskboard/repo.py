"""SQL, as plain functions over a connection. `Database.run` supplies the transaction."""

import sqlite3
from datetime import UTC, date, datetime

import msgspec

from taskboard.models import (
    Event,
    ProjectOut,
    TaskIn,
    TaskOut,
    TaskPatch,
    TaskQuery,
    UserOut,
)


class VersionConflict(Exception):
    """The task changed since the client read it."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# -- users ------------------------------------------------------------------------------------


def create_user(conn: sqlite3.Connection, email: str, password_hash: str) -> UserOut:
    cursor = conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (email, password_hash, now()),
    )
    assert cursor.lastrowid is not None
    return UserOut(id=cursor.lastrowid, email=email)


def find_user(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    found: sqlite3.Row | None = conn.execute(
        "SELECT id, email, password_hash FROM users WHERE email = ?", (email,)
    ).fetchone()
    return found


def get_user(conn: sqlite3.Connection, user_id: int) -> UserOut | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, email FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return None if row is None else UserOut(id=row["id"], email=row["email"])


# -- projects ---------------------------------------------------------------------------------


def _project(row: sqlite3.Row) -> ProjectOut:
    return ProjectOut(
        id=row["id"], name=row["name"], created_at=datetime.fromisoformat(row["created_at"])
    )


def list_projects(conn: sqlite3.Connection, owner_id: int) -> list[ProjectOut]:
    rows = conn.execute(
        "SELECT id, name, created_at FROM projects WHERE owner_id = ? ORDER BY id", (owner_id,)
    ).fetchall()
    return [_project(row) for row in rows]


def create_project(conn: sqlite3.Connection, owner_id: int, name: str) -> ProjectOut:
    created = now()
    cursor = conn.execute(
        "INSERT INTO projects (owner_id, name, created_at) VALUES (?, ?, ?)",
        (owner_id, name, created),
    )
    assert cursor.lastrowid is not None
    return ProjectOut(id=cursor.lastrowid, name=name, created_at=datetime.fromisoformat(created))


def get_project(conn: sqlite3.Connection, owner_id: int, project_id: int) -> ProjectOut | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, name, created_at FROM projects WHERE id = ? AND owner_id = ?",
        (project_id, owner_id),
    ).fetchone()
    return None if row is None else _project(row)


def rename_project(
    conn: sqlite3.Connection, owner_id: int, project_id: int, name: str
) -> ProjectOut | None:
    conn.execute(
        "UPDATE projects SET name = ? WHERE id = ? AND owner_id = ?", (name, project_id, owner_id)
    )
    return get_project(conn, owner_id, project_id)


def delete_project(conn: sqlite3.Connection, owner_id: int, project_id: int) -> bool:
    cursor = conn.execute(
        "DELETE FROM projects WHERE id = ? AND owner_id = ?", (project_id, owner_id)
    )
    return cursor.rowcount == 1


# -- tasks ------------------------------------------------------------------------------------

_TASK_COLUMNS = (
    "id, project_id, title, description, status, priority, due, version, created_at, updated_at"
)
_PATCHABLE = ("title", "description", "status", "priority", "due")


def _task(row: sqlite3.Row) -> TaskOut:
    return TaskOut(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        priority=row["priority"],
        due=None if row["due"] is None else date.fromisoformat(row["due"]),
        version=row["version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _record(conn: sqlite3.Connection, project_id: int, kind: str, task_id: int) -> Event:
    created = now()
    cursor = conn.execute(
        "INSERT INTO events (project_id, kind, task_id, created_at) VALUES (?, ?, ?, ?)",
        (project_id, kind, task_id, created),
    )
    assert cursor.lastrowid is not None
    return Event(
        id=cursor.lastrowid,
        project_id=project_id,
        kind=kind,
        task_id=task_id,
        created_at=datetime.fromisoformat(created),
    )


def list_tasks(
    conn: sqlite3.Connection, project_id: int, query: TaskQuery
) -> tuple[list[TaskOut], int]:
    where = ["project_id = ?"]
    args: list[int | str] = [project_id]
    if query.status is not None:
        where.append("status = ?")
        args.append(query.status)
    if query.q:
        # LIKE with the user's text escaped, so % and _ match themselves
        escaped = query.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("title LIKE ? ESCAPE '\\'")
        args.append(f"%{escaped}%")
    clause = " AND ".join(where)
    total: int = conn.execute(f"SELECT COUNT(*) FROM tasks WHERE {clause}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE {clause} ORDER BY id LIMIT ? OFFSET ?",
        [*args, query.limit, query.offset],
    ).fetchall()
    return [_task(row) for row in rows], total


def get_task(conn: sqlite3.Connection, project_id: int, task_id: int) -> TaskOut | None:
    row: sqlite3.Row | None = conn.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ? AND project_id = ?", (task_id, project_id)
    ).fetchone()
    return None if row is None else _task(row)


def create_task(conn: sqlite3.Connection, project_id: int, data: TaskIn) -> tuple[TaskOut, Event]:
    stamp = now()
    cursor = conn.execute(
        "INSERT INTO tasks (project_id, title, description, status, priority, due, created_at,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            data.title,
            data.description,
            data.status,
            data.priority,
            None if data.due is None else data.due.isoformat(),
            stamp,
            stamp,
        ),
    )
    assert cursor.lastrowid is not None
    task = get_task(conn, project_id, cursor.lastrowid)
    assert task is not None
    return task, _record(conn, project_id, "task.created", task.id)


def update_task(
    conn: sqlite3.Connection, project_id: int, task_id: int, patch: TaskPatch
) -> tuple[TaskOut, Event] | None:
    """The updated task, `None` if it does not exist; raises `VersionConflict` when `patch.version`
    is given and is no longer the task's version."""
    assignments: list[str] = []
    args: list[object] = []
    for column in _PATCHABLE:
        value = getattr(patch, column)
        if value is msgspec.UNSET:
            continue
        assignments.append(f"{column} = ?")
        args.append(value.isoformat() if isinstance(value, date) else value)
    where = "id = ? AND project_id = ?"
    where_args: list[int] = [task_id, project_id]
    if patch.version is not None:
        where += " AND version = ?"
        where_args.append(patch.version)
    cursor = conn.execute(
        f"UPDATE tasks SET {', '.join([*assignments, 'version = version + 1', 'updated_at = ?'])}"
        f" WHERE {where}",
        [*args, now(), *where_args],
    )
    if cursor.rowcount == 0:
        if get_task(conn, project_id, task_id) is None:
            return None
        raise VersionConflict
    task = get_task(conn, project_id, task_id)
    assert task is not None
    return task, _record(conn, project_id, "task.updated", task_id)


def delete_task(conn: sqlite3.Connection, project_id: int, task_id: int) -> Event | None:
    cursor = conn.execute(
        "DELETE FROM tasks WHERE id = ? AND project_id = ?", (task_id, project_id)
    )
    if cursor.rowcount == 0:
        return None
    return _record(conn, project_id, "task.deleted", task_id)


# -- events -----------------------------------------------------------------------------------


def events_since(conn: sqlite3.Connection, project_id: int, last_id: int) -> list[Event]:
    rows = conn.execute(
        "SELECT id, project_id, kind, task_id, created_at FROM events"
        " WHERE project_id = ? AND id > ? ORDER BY id LIMIT 500",
        (project_id, last_id),
    ).fetchall()
    return [
        Event(
            id=row["id"],
            project_id=row["project_id"],
            kind=row["kind"],
            task_id=row["task_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        for row in rows
    ]
