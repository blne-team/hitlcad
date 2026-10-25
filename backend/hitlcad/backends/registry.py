"""Backend discovery: built-ins plus ``hitlcad.backends`` entry points.

An entry point must resolve to a callable ``factory(settings) -> CADBackend``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import entry_points

from hitlcad.backends.base import BackendInfo, CADBackend
from hitlcad.config import Settings

log = logging.getLogger(__name__)

BackendFactory = Callable[[Settings], CADBackend]


def _build123d(settings: Settings) -> CADBackend:
    from hitlcad.backends.build123d.backend import Build123dBackend

    return Build123dBackend(
        timeout_s=settings.build_timeout_s,
        memory_mb=settings.build_memory_mb,
        mesh_tolerance_mm=settings.mesh_tolerance_mm,
        mesh_angular_tolerance=settings.mesh_angular_tolerance,
    )


def _blender(settings: Settings) -> CADBackend:
    from hitlcad.backends.blender.backend import BlenderBackend

    return BlenderBackend(
        binary=settings.blender_binary,
        timeout_s=settings.build_timeout_s,
        memory_mb=settings.build_memory_mb,
        mesh_tolerance_mm=settings.mesh_tolerance_mm,
        mesh_angular_tolerance=settings.mesh_angular_tolerance,
    )


BUILTIN: dict[str, BackendFactory] = {"build123d": _build123d, "blender": _blender}


def discover() -> dict[str, BackendFactory]:
    found = dict(BUILTIN)
    for ep in entry_points(group="hitlcad.backends"):
        try:
            found[ep.name] = ep.load()
        except Exception:  # a broken plugin must not take the service down
            log.exception("failed to load CAD backend plugin %s", ep.name)
    return found


class BackendRegistry:
    def __init__(self, backends: dict[str, CADBackend]) -> None:
        self._backends = backends

    @classmethod
    def from_settings(cls, settings: Settings) -> BackendRegistry:
        factories = discover()
        missing = [n for n in settings.backends if n not in factories]
        if missing:
            raise ValueError(f"unknown CAD backends {missing}; known: {sorted(factories)}")
        return cls({n: factories[n](settings) for n in settings.backends})

    def get(self, name: str) -> CADBackend:
        try:
            return self._backends[name]
        except KeyError:
            raise LookupError(f"unknown backend {name!r}; enabled: {', '.join(self.names())}") from None

    def names(self) -> list[str]:
        return list(self._backends)

    def all(self) -> list[CADBackend]:
        return list(self._backends.values())

    def available(self) -> list[CADBackend]:
        return [b for b in self.all() if b.availability()[0]]

    def describe(self) -> list[BackendInfo]:
        return [b.describe() for b in self.all()]
