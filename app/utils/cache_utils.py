"""Simple in-memory TTL cache utilities.

Provides a lightweight TTL cache since cachetools is not available.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class _TTLCacheEntry(Generic[T]):
    __slots__ = ("value", "expires_at")

    def __init__(self, value: T, ttl_seconds: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl_seconds


class TTLCache(Generic[T]):
    """Thread-safe TTL cache with optional max size."""

    def __init__(self, *, maxsize: int = 128, ttl: float = 60.0):
        self._maxsize = max(maxsize, 1)
        self._ttl = max(ttl, 0.0)
        self._data: dict[str, _TTLCacheEntry[T]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[T]:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if now >= entry.expires_at:
                self._data.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: T) -> None:
        now = time.monotonic()
        with self._lock:
            # Evict expired entries first
            expired = [k for k, e in self._data.items() if now >= e.expires_at]
            for k in expired:
                self._data.pop(k, None)

            # If still at capacity, evict oldest (FIFO)
            if len(self._data) >= self._maxsize and key not in self._data:
                oldest = next(iter(self._data))
                self._data.pop(oldest, None)

            self._data[key] = _TTLCacheEntry(value, self._ttl)

    def pop(self, key: str) -> Optional[T]:
        with self._lock:
            entry = self._data.pop(key, None)
            if entry is None:
                return None
            if time.monotonic() >= entry.expires_at:
                return None
            return entry.value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, e in self._data.items() if now >= e.expires_at]
            for k in expired:
                self._data.pop(k, None)
            return len(self._data)


def cached_ttl(ttl: float = 60.0, maxsize: int = 128):
    """Decorator that caches function results with TTL."""
    cache = TTLCache[Any](maxsize=maxsize, ttl=ttl)

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            # Simple key: function name + args + kwargs
            key_parts = [func.__name__]
            key_parts.extend(str(a) for a in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            key = "|".join(key_parts)

            result = cache.get(key)
            if result is not None:
                return result

            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        wrapper._cache = cache  # type: ignore[attr-defined]
        return wrapper

    return decorator
