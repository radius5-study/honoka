"""A task board API built on honoka. `create_app` assembles it; see the README."""

from taskboard.app import create_app
from taskboard.settings import Settings

__all__ = ["Settings", "create_app"]
