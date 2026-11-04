"""Values kept in a process's memory, the least recently used let go first: the standard library only, so a build
worker's runtime (`execution.runtime`) uses it in any Python, Blender's included."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class MemoryLru(Generic[K, V]):
    """Values in this process by key, the least recently used let go first once they measure more than `capacity`
    (each `size`: one by default, so the capacity is a count). Safe across threads."""

    def __init__(self, capacity: int, size: Callable[[V], int] = lambda _: 1) -> None:
        self.capacity = capacity
        self.size = size
        self.held = 0
        self._kept: OrderedDict[K, tuple[V, int]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        with self._lock:
            found = self._kept.get(key)
            if found is None:
                return None
            self._kept.move_to_end(key)
            return found[0]

    def put(self, key: K, value: V) -> V:
        size = self.size(value)
        with self._lock:
            if (old := self._kept.pop(key, None)) is not None:
                self.held -= old[1]
            if size <= self.capacity:
                self._kept[key] = value, size
                self.held += size
            while self.held > self.capacity:
                _, (_, gone) = self._kept.popitem(last=False)
                self.held -= gone
        return value

    def get_or(self, key: K, make: Callable[[], V]) -> V:
        """Its value, made and kept when there is none (two threads asking at once may both make it)."""
        found = self.get(key)
        return found if found is not None else self.put(key, make())

    def pop(self, key: K) -> None:
        with self._lock:
            if (old := self._kept.pop(key, None)) is not None:
                self.held -= old[1]

    def __len__(self) -> int:
        return len(self._kept)
