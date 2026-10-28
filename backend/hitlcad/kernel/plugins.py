"""Plugins: what a design can say and what hitlcad makes of it, hitlcad's own or installed.

A plugin is a module registered under the ``hitlcad.plugins`` entry-point group, with `hookimpl` functions for
the hooks of `Spec`; hitlcad's own (``hitlcad.builtin.*``) register the same way. Each kind of contribution is
gathered once, the first time it is asked for, into the `Registry`, with the plugin it came from:

    connectors, mates, relations, checks, processes   the design model's members, gathered while
                                                       `hitlcad.core.design` is imported, to build its unions
    backends, catalogs, exporters, solvers,           everything else, gathered when first used
    analyses, materials, model packages, error kinds, skill references

So a plugin's module, and the members it contributes, import nothing that imports `hitlcad.core.design`; what
else a hook returns, it imports inside the hook. A plugin that does not load, a hook that fails, and two plugins
giving one name are diagnostics (`Registry.failures`), and the rest goes on without them. Each requirement check
and process contributes the kinds of its results to the error catalog: `requirement.<kind>`, with the check's
docstring and `hint`, and `manufacturing.<finding>` for each of a process's `findings`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache, cached_property
from importlib.metadata import PackageNotFoundError, entry_points, version
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Never, Union

import pluggy
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import Field

from hitlcad.kernel.errors import (
    HITLCAD_BUG,
    PLUGIN_CONFLICT,
    PLUGIN_ERROR,
    PLUGIN_LOAD_FAILED,
    PLUGIN_MISSING,
    Diagnostic,
    ErrorKind,
    HitlcadError,
    define,
    register,
)
from hitlcad.kernel.model import Model

if TYPE_CHECKING:
    from hitlcad.analysis.base import Analysis
    from hitlcad.backends.base import CADBackend
    from hitlcad.catalogs import CatalogProvider
    from hitlcad.config import Settings
    from hitlcad.core.loops import Solver
    from hitlcad.exporters import Exporter

GROUP = "hitlcad.plugins"
BUILTIN = "hitlcad.builtin."

hookspec = pluggy.HookspecMarker("hitlcad")
hookimpl = pluggy.HookimplMarker("hitlcad")


@dataclass(frozen=True)
class ModelPackage:
    """Code a design's part files import: catalog helpers, building blocks. `root` is the directory its package
    is in (what a build process's ``sys.path`` gets); `package` is the package's name in it."""

    root: Path
    package: str
    #: The backends whose part files may import it; a backend whose Python is not hitlcad's (Blender's) runs
    #: only packages that need nothing but the standard library and the backend's own modules.
    backends: tuple[str, ...]
    summary: str = ""  # public helpers and their purpose, discoverable without importing backend modules

    def files(self) -> list[Path]:
        return sorted((self.root / self.package.replace(".", "/")).rglob("*.py"))


BackendFactory = Callable[["Settings"], "CADBackend"]


class Spec:
    """The hooks a plugin implements, each returning what it contributes."""

    @hookspec
    def hitlcad_connectors(self) -> list[type]:
        """Connector kinds: `core.structure.Connector` subclasses with a `kind` literal."""

    @hookspec
    def hitlcad_mates(self) -> list[type]:
        """Mate types: `core.structure.Mate` subclasses with a `type` literal."""

    @hookspec
    def hitlcad_relations(self) -> list[type]:
        """Relation types: `core.structure.Relation` subclasses with a `type` literal."""

    @hookspec
    def hitlcad_checks(self) -> list[type]:
        """Requirement checks: `core.checks.Check` subclasses with a `kind` literal and a `hint`."""

    @hookspec
    def hitlcad_processes(self) -> list[type]:
        """Manufacturing processes: `core.checks.Process` subclasses with a `process` literal and `findings`."""

    @hookspec
    def hitlcad_backends(self) -> dict[str, BackendFactory]:
        """CAD backends by name, each a factory taking hitlcad's settings."""

    @hookspec
    def hitlcad_catalogs(self) -> list[CatalogProvider]:
        """Catalog providers that `search_catalog` searches."""

    @hookspec
    def hitlcad_exporters(self) -> list[Exporter]:
        """Exporters a design names in `outputs`."""

    @hookspec
    def hitlcad_solvers(self) -> dict[str, Solver]:
        """Loop solvers by name: what moves a closed loop of mates for motion analysis (`Settings.solver`)."""

    @hookspec
    def hitlcad_analyses(self) -> list[Analysis]:
        """Analyses of a new kind, run at their `stage` with hitlcad's own."""

    @hookspec
    def hitlcad_materials(self) -> list[Path]:
        """materials.json files, read in order: a later one's materials replace an earlier one's."""

    @hookspec
    def hitlcad_model_packages(self) -> list[ModelPackage]:
        """Packages a design's part files import."""

    @hookspec
    def hitlcad_error_kinds(self) -> list[ErrorKind]:
        """The kinds of the plugin's own diagnostics and results."""

    @hookspec
    def hitlcad_skill_references(self) -> dict[str, str]:
        """Reference files for the agents' `hitlcad` skill, by file name: how to use what the plugin adds."""


class Plugin(Model):
    name: str = Field(description="Its entry point's")
    module: str
    distribution: str
    version: str
    builtin: bool = Field(description="One of hitlcad's own")


#: The design model's members: each hook, the field that names a member in design.json, and what one is.
MEMBERS = {"connectors": ("kind", "connector kind"), "mates": ("type", "mate type"),
           "relations": ("type", "relation type"), "checks": ("kind", "requirement check"),
           "processes": ("process", "process")}  # fmt: skip


class Registry:
    def __init__(self) -> None:
        self.manager = pluggy.PluginManager("hitlcad")
        self.manager.add_hookspecs(Spec)
        self.plugins: dict[str, Plugin] = {}
        self.failures: list[Diagnostic] = []
        self._origin: dict[int, Plugin] = {}  # id of each contribution -> the plugin it came from
        for ep in entry_points(group=GROUP):
            dist = ep.dist.name if ep.dist else ep.module
            plugin = Plugin(name=ep.name, module=ep.module, distribution=dist, version=ep.dist.version if ep.dist else "",
                            builtin=ep.module.startswith(BUILTIN))  # fmt: skip
            try:
                self.manager.register(ep.load(), name=ep.name)
            except Exception as e:  # a broken plugin must not take hitlcad down
                self.failures.append(PLUGIN_LOAD_FAILED.diagnostic(
                    f"plugin {ep.name} ({dist}) did not load: {type(e).__name__}: {e}"))  # fmt: skip
                continue
            self.plugins[ep.name] = plugin

    # ------------------------------------------------------------------ gathering

    def _gather(self, hook: str) -> list[tuple[Plugin, Any]]:
        """Each plugin's contribution to a hook, hitlcad's own first, in the order they registered."""
        impls = getattr(self.manager.hook, hook).get_hookimpls()
        order = list(self.plugins)
        out = []
        for impl in sorted(impls, key=lambda i: (not self.plugins[i.plugin_name].builtin, order.index(i.plugin_name))):
            plugin = self.plugins[impl.plugin_name]
            try:
                out.append((plugin, impl.function()))
            except Exception as e:
                self.failures.append(self.failed(plugin, f"its {hook} hook failed", e))
        return out

    def _named(self, hook: str, key: Callable[[Any], str], what: str) -> list[tuple[Plugin, Any]]:
        """A hook's contributions, one each per name: two plugins giving one name both lose it."""
        found = [(plugin, item) for plugin, items in self._gather(hook)
                 for item in (items.items() if isinstance(items, dict) else items)]  # fmt: skip
        names: dict[str, list[Plugin]] = {}
        for plugin, item in found:
            names.setdefault(key(item), []).append(plugin)
        for name, by in names.items():
            if len(by) > 1:
                self.failures.append(PLUGIN_CONFLICT.diagnostic(
                    f"{what} {name!r} is given by {' and '.join(p.distribution for p in by)}: neither is used"))  # fmt: skip
        kept = [(plugin, item) for plugin, item in found if len(names[key(item)]) == 1]
        for plugin, item in kept:
            self._origin[id(item)] = plugin
        return kept

    def _members(self, hook: str) -> tuple[type, ...]:
        field, what = MEMBERS[hook]
        return tuple(item for _, item in self._named(f"hitlcad_{hook}", lambda c: _literal(c, field), what))

    def origin(self, contribution: Any) -> Plugin | None:
        """The plugin a contribution came from: a member or its class, an exporter, a provider."""
        return self._origin.get(id(contribution)) or self._origin.get(id(type(contribution)))

    def failed(self, plugin: Plugin | None, what: str, e: BaseException) -> Diagnostic:
        """What a plugin's code raising means: a bug in hitlcad when it is hitlcad's own."""
        text = f"{what}: {type(e).__name__}: {e}"
        if plugin is None or plugin.builtin:
            return HITLCAD_BUG.diagnostic(text)
        return PLUGIN_ERROR.diagnostic(f"{plugin.distribution} {plugin.version}: {text}")

    # ------------------------------------------------------------------ the design model's members

    @cached_property
    def connectors(self) -> tuple[type, ...]:
        return self._members("connectors")

    @cached_property
    def mates(self) -> tuple[type, ...]:
        return self._members("mates")

    @cached_property
    def relations(self) -> tuple[type, ...]:
        return self._members("relations")

    @cached_property
    def checks(self) -> tuple[type, ...]:
        found = self._members("checks")
        for check in found:
            plugin = self._origin[id(check)]
            title = " ".join((check.__doc__ or check.__name__).split("\n\n")[0].split())
            self._define(plugin, f"requirement.{_literal(check, 'kind')}", title, check.hint,
                         "hitlcad: design.json reference, Requirements")  # fmt: skip
        return found

    @cached_property
    def processes(self) -> tuple[type, ...]:
        found = self._members("processes")
        for process in found:
            plugin = self._origin[id(process)]
            for name, (title, hint) in process.findings.items():
                self._define(plugin, f"manufacturing.{name}", title, hint,
                             "hitlcad: design.json reference, Manufacturing")  # fmt: skip
        return found

    def _define(self, plugin: Plugin, code: str, title: str, hint: str, skill: str) -> None:
        try:
            define(code, "design", title, hint, skill)
        except ValueError as e:
            self.failures.append(PLUGIN_CONFLICT.diagnostic(f"{plugin.distribution}: {e}"))

    def union(self, members: tuple[type, ...], field: str) -> Any:
        """The members as one type, told apart by `field`: what a design.json field holds."""
        if not members:
            return Never
        if len(members) == 1:
            return members[0]
        return Annotated[Union[members], Field(discriminator=field)]  # noqa: UP007

    # ------------------------------------------------------------------ everything else

    @cached_property
    def backends(self) -> dict[str, BackendFactory]:
        named = self._named("hitlcad_backends", lambda f: f[0], "backend")
        return dict(item for _, item in named)

    def _flat(self, hook: str) -> list[tuple[Plugin, Any]]:
        return [(plugin, item) for plugin, items in self._gather(hook) for item in items]

    @cached_property
    def catalogs(self) -> dict[str, CatalogProvider]:
        return {p.name: p for _, p in self._named("hitlcad_catalogs", lambda p: p.name, "catalog provider")}

    @cached_property
    def exporters(self) -> dict[str, Exporter]:
        return {e.name: e for _, e in self._named("hitlcad_exporters", lambda e: e.name, "exporter")}

    @cached_property
    def solvers(self) -> dict[str, Solver]:
        return dict(item for _, item in self._named("hitlcad_solvers", lambda s: s[0], "loop solver"))

    @cached_property
    def analyses(self) -> list[Analysis]:
        return [a for _, a in self._named("hitlcad_analyses", lambda a: a.name, "analysis")]

    @cached_property
    def materials(self) -> list[Path]:
        return [path for _, path in self._flat("hitlcad_materials")]

    @cached_property
    def model_packages(self) -> list[ModelPackage]:
        return [package for _, package in self._flat("hitlcad_model_packages")]

    @cached_property
    def skill_references(self) -> dict[str, str]:
        found = [(name, text) for _, refs in self._gather("hitlcad_skill_references") for name, text in refs.items()]
        return dict(found)

    def versions(self) -> dict[str, str]:
        """The installed plugins that are not hitlcad's own, by distribution: what a build records it ran with."""
        return {p.distribution: p.version for p in self.plugins.values() if not p.builtin}

    def needs(self, required: dict[str, str]) -> HitlcadError | None:
        """What of the plugins a design names (distribution -> version specifier) is not installed and loaded."""
        problems = []
        loaded = {p.distribution for p in self.plugins.values()}
        for dist, wanted in required.items():
            try:
                accepts = SpecifierSet(wanted)
            except InvalidSpecifier:
                problems.append(f"{dist} {wanted!r} is not a version specifier like '>=0.1'")
                continue
            try:
                found = version(dist)
            except PackageNotFoundError:
                problems.append(f"{dist} {wanted} is not installed")
                continue
            if found not in accepts:
                problems.append(f"{dist} {wanted} is needed, {found} is installed")
            elif dist not in loaded:
                problems.append(f"{dist} {found} is installed but did not load (`hitlcad doctor` says why)")
        return PLUGIN_MISSING("design.json needs " + "; ".join(problems)) if problems else None

    def load_error_kinds(self) -> None:
        """Enter the plugins' own kinds in the catalog."""
        for plugin, kind in self._flat("hitlcad_error_kinds"):
            try:
                register(kind)
            except ValueError as e:
                self.failures.append(PLUGIN_CONFLICT.diagnostic(f"{plugin.distribution}: {e}"))


def _literal(cls: type, field: str) -> str:
    """The name a member class is known by in design.json: its literal `field`."""
    return cls.model_fields[field].annotation.__args__[0]  # type: ignore[attr-defined, no-any-return]


@cache
def registry() -> Registry:
    found = Registry()
    found.load_error_kinds()
    return found
