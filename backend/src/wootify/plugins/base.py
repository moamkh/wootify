"""Provider-neutral plugin descriptor."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from wootify.domain.platform import PlatformCapabilities, PlatformKey


AdapterFactory = Callable[[str, dict[str, Any]], Any]


@dataclass(frozen=True)
class PlatformPlugin:
    """Describe one platform key and its existing provider implementations.

    ``connector`` and ``adapter_factory`` are intentionally opaque to the
    domain. They are supplied by the composition root and let the old
    connector/adapter objects remain unchanged during the migration.
    """

    key: str
    display_name: str
    connector: Any
    source_prefix: str
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    adapter_factory: AdapterFactory | None = None
    experimental: bool = False
    family: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", str(PlatformKey(self.key)))
        object.__setattr__(self, "source_prefix", str(self.source_prefix or self.key).upper())
        object.__setattr__(self, "capabilities", MappingProxyType({str(k): bool(v) for k, v in self.capabilities.items()}))

    @property
    def platform_key(self) -> PlatformKey:
        return PlatformKey(self.key)

    @property
    def capability_set(self) -> PlatformCapabilities:
        return PlatformCapabilities(self.capabilities)

    def supports(self, capability: str) -> bool:
        return self.capability_set.supports(capability)

    def build_adapter(self, instance_key: str, config: dict[str, Any]) -> Any:
        if self.adapter_factory is None:
            raise ValueError(f"Platform '{self.key}' does not provide an adapter")
        return self.adapter_factory(instance_key, config)
