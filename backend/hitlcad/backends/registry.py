"""The CAD backends this server builds with: those `Settings.backends` enables, of those plugins give."""

from __future__ import annotations

from hitlcad.backends.base import BackendInfo, CADBackend
from hitlcad.config import Settings
from hitlcad.kernel.errors import PLUGIN_MISSING, REQUEST_INVALID
from hitlcad.kernel.plugins import registry


class BackendRegistry:
    def __init__(self, backends: dict[str, CADBackend]) -> None:
        self._backends = backends

    @classmethod
    def from_settings(cls, settings: Settings) -> BackendRegistry:
        found = registry()
        for name in settings.backends:
            if name not in found.backends:  # enabled, but no plugin gives it: hitlcad runs without it, and says so
                found.failures.append(PLUGIN_MISSING.diagnostic(
                    f"backend {name} is enabled (HITLCAD_BACKENDS) but no installed plugin gives it; plugins give: "
                    f"{', '.join(found.backends) or 'none'}"))  # fmt: skip
        return cls({n: found.backends[n](settings) for n in settings.backends if n in found.backends})

    def get(self, name: str) -> CADBackend:
        try:
            return self._backends[name]
        except KeyError:
            raise REQUEST_INVALID(f"unknown backend {name!r}; enabled: {', '.join(self.names())}") from None

    def names(self) -> list[str]:
        return list(self._backends)

    def all(self) -> list[CADBackend]:
        return list(self._backends.values())

    def available(self) -> list[CADBackend]:
        return [b for b in self.all() if b.availability()[0]]

    def describe(self) -> list[BackendInfo]:
        return [b.describe() for b in self.all()]
