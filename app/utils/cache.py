"""
Module Overview
---------------
Purpose: Reusable utility helpers shared across services and connectors.
Documentation Standard: module/class/public-method docstrings.
"""
import time
from typing import Any, Dict, Optional

class SimpleCache:
    """Represents simple cache."""
    def __init__(self):
        """Initialize the instance."""
        self._store: Dict[str, Any] = {}
        self._expires: Dict[str, float] = {}

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None):
        """Set."""
        self._store[key] = value
        if ttl_seconds:
            self._expires[key] = time.time() + ttl_seconds

    def get(self, key: str) -> Any:
        """Get cached value if present and not expired."""
        if key in self._expires and time.time() > self._expires[key]:
            self.delete(key)
            return None
        return self._store.get(key)

    def has(self, key: str) -> bool:
        """Has."""
        return self.get(key) is not None

    def delete(self, key: str):
        """Delete a cached key and its expiration metadata."""
        self._store.pop(key, None)
        self._expires.pop(key, None)

cache = SimpleCache()

