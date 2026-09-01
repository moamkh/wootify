"""Platform value objects shared by application and provider boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class PlatformKey:
    """Normalized platform identifier used at application boundaries."""

    value: str

    def __post_init__(self) -> None:
        normalized = str(self.value or "").strip().lower()
        if not normalized:
            raise ValueError("platform key cannot be empty")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class PlatformCapabilities:
    """Immutable capability flags exposed by a platform plugin."""

    values: Mapping[str, bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType({str(k): bool(v) for k, v in self.values.items()}))

    def supports(self, capability: str) -> bool:
        return bool(self.values.get(str(capability or "").strip().lower(), False))

    def as_dict(self) -> dict[str, bool]:
        return {str(key): bool(value) for key, value in self.values.items()}
