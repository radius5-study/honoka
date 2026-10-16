from collections.abc import Awaitable, Callable

from honoka.context import Context
from honoka.response import Response

type Handler = Callable[[Context], Response | Awaitable[Response]]
type Next = Callable[[], Awaitable[Response]]
type Middleware = Callable[[Context, Next], Awaitable[Response]]
