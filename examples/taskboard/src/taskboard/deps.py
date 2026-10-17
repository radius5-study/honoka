"""What handlers read from the Context: app-wide services and the current user."""

from honoka_jwt import JWT_PAYLOAD

from honoka import Context, HTTPError, Key
from taskboard.db import Database
from taskboard.events import EventBus
from taskboard.models import ProjectOut
from taskboard.settings import Settings
from taskboard.throttle import Throttle

SETTINGS = Key[Settings]("settings")
DB = Key[Database]("db")
BUS = Key[EventBus]("bus")
LOGIN_THROTTLE = Key[Throttle]("login_throttle")
REGISTER_THROTTLE = Key[Throttle]("register_throttle")
PROJECT = Key[ProjectOut]("project")  # set by `project_access` on routes below a project


def client_host(c: Context) -> str:
    """The peer's address. Behind a reverse proxy this is the proxy, so every client shares one
    count; read the forwarded header from a proxy you trust if that matters."""
    return c.req.client.rpartition(":")[0] or c.req.client


def current_user_id(c: Context) -> int:
    """The user the verified access token was issued to."""
    subject = c.get(JWT_PAYLOAD).get("sub")
    if not isinstance(subject, str) or not subject.isdecimal():
        raise HTTPError(401, "invalid_token")
    return int(subject)
