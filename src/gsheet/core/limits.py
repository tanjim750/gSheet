"""Process-local rolling-minute project and connection request reservations.

No credentials or sheet data are retained here. One CredentialManager is the
connection lifetime; reuse it across clients to retain the connection budget.
Unspecified projects share a conservative application-default project bucket.
Independent worker processes still need application-side quota coordination.
"""

from collections import deque
from threading import RLock
from time import monotonic
from weakref import WeakKeyDictionary

from ..config import OperationalConfig
from .exceptions import APIClientError, APIErrorCode

_lock = RLock()
_projects: dict[tuple[str | None, str], deque[float]] = {}
_connections: WeakKeyDictionary = WeakKeyDictionary()


def reserve(settings: OperationalConfig, connection: object, *, read: bool,
            request_id: str, attempts: int) -> None:
    if not settings.throttling_enabled:
        return
    kind = "read" if read else "write"
    key = (settings.quota_project_id, kind)
    with _lock:
        now = monotonic()
        # Expired inactive project entries need not live for the whole process.
        for project_key in list(_projects):
            history = _projects[project_key]
            while history and history[0] <= now - 60:
                history.popleft()
            if not history:
                del _projects[project_key]
        project = _projects.setdefault(key, deque())
        histories = _connections.setdefault(connection, {})
        local = histories.setdefault(key, deque())
        while local and local[0] <= now - 60:
            local.popleft()
        blocked = []
        for history, limit in (
                (project, getattr(settings, f"{kind}_requests_per_minute_per_project")),
                (local, getattr(settings, f"{kind}_requests_per_minute_per_user"))):
            if len(history) >= limit:
                # When callers lower a policy, wait until enough entries expire.
                blocked.append(max(0.0, 60 - (now - history[len(history) - limit])))
        if blocked:
            raise APIClientError(APIErrorCode.THROTTLED, retryable=True, attempts=attempts,
                                 request_id=request_id, retry_after_seconds=max(blocked))
        project.append(now)
        local.append(now)
