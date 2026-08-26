"""Dependency-inversion ports for application services.

These protocols deliberately contain only behavior needed by use cases. The
existing connector and SQLAlchemy implementations can satisfy them without
being imported by the domain package.
"""

from __future__ import annotations

from typing import Any, Protocol


class ConnectorPort(Protocol):
    """Provider connector operations consumed by application workflows."""

    async def close(self) -> None:
        """Release provider resources."""


class UnitOfWork(Protocol):
    """Transaction boundary for application use cases."""

    def __enter__(self) -> "UnitOfWork":
        """Open a transaction scope."""

    def __exit__(self, *exc_info: Any) -> None:
        """Close or roll back a transaction scope."""

    def commit(self) -> None:
        """Commit the current transaction."""
