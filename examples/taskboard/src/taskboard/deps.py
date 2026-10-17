"""What handlers read from the Context: app-wide services and the current user."""

from honoka_jwt import JWT_PAYLOAD

from honoka import Context, HTTPError, Key
from taskboard.db import Database
from taskboard.events import EventBus
from taskboard.models import ProjectOut
from taskboard.settings import Settings

SETTINGS = Key[Settings]("settings")
DB = Key[Database]("db")
BUS = Key[EventBus]("bus")
PROJECT = Key[ProjectOut]("project")  # set by `project_access` on routes below a project


def current_user_id(c: Context) -> int:
    """The user the verified access token was issued to."""
    subject = c.get(JWT_PAYLOAD).get("sub")
    if not isinstance(subject, str) or not subject.isdecimal():
        raise HTTPError(401, "invalid_token")
    return int(subject)
