"""Small client-owned metadata cache; never a cross-connection data store."""

from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from typing import Any


class MetadataCache:
    """Caller serializes access; return copies so callers cannot mutate entries."""

    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> tuple[Any, float] | None:
        now = monotonic()
        self._expire(now)
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return deepcopy(entry[1]), now - entry[0]

    def put(self, key: str, value: Any) -> None:
        now = monotonic()
        self._expire(now)
        self._entries[key] = (now, deepcopy(value))
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def _expire(self, now: float) -> None:
        for key in [k for k, (created, _) in self._entries.items() if now - created >= self._ttl]:
            del self._entries[key]

    def clear(self) -> None:
        self._entries.clear()
