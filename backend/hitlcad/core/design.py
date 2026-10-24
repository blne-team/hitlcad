"""design.json, format 2: a design is an assembly of instances of its parts and components.

Names are checked here, when the file loads. How the names relate (mates between declared
connectors, every instance mated to ground, components that exist) is checked by the structure
analysis of each build, which reports problems with their addresses instead of refusing the file.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from hitlcad.core.address import End, InstancePath, check_name
from hitlcad.core.checks import Manufacturing, Requirement
from hitlcad.core.structure import (
    ComponentSpec,
    FastenerSet,
    MateSpec,
    PartSpec,
    RelationSpec,
    Verification,
)
from hitlcad.core.types import DesignBrief, Model, ParamSpec, Value

FORMAT = 2


class DesignSpec(Model):
    hitlcad: Literal[2] = FORMAT
    name: str
    number: str = Field(default="", description="The user's own part number for the whole design")
    backend: str = Field(description="Backend for parts that do not set their own")
    parameters: list[ParamSpec] = Field(default_factory=list)
    configurations: dict[str, dict[str, Value]] = Field(
        default_factory=dict, description="Named sets of parameter values over `parameters`: product variants"
    )
    ground: str | None = Field(default=None, description="The instance every mate chain leads back to")
    parts: dict[str, PartSpec] = Field(default_factory=dict)
    components: dict[str, ComponentSpec] = Field(default_factory=dict)
    exports: dict[str, str] = Field(
        default_factory=dict, description="Connector name -> connector of a direct child, for designs that use this one"
    )
    mates: list[MateSpec] = Field(default_factory=list)
    relations: list[RelationSpec] = Field(default_factory=list)
    fasteners: list[FastenerSet] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    manufacturing: Manufacturing | None = Field(default=None, description="Default process for made parts")
    verification: Verification = Field(default_factory=Verification)
    outputs: list[str] = Field(default_factory=list, description="Exporters to run on every successful build")
    brief: DesignBrief | None = None

    @field_validator("ground")
    @classmethod
    def _ground(cls, v: str | None) -> str | None:
        if v is not None:
            (segment,) = InstancePath.parse(v).segments
        return v

    @field_validator("exports")
    @classmethod
    def _exports(cls, v: dict[str, str]) -> dict[str, str]:
        for name, end in v.items():
            check_name(name, "export")
            End.parse(end)
        return v

    @model_validator(mode="after")
    def _names(self) -> DesignSpec:
        seen: dict[str, str] = {}
        for what, names in (("parameter", [p.name for p in self.parameters]), ("part", list(self.parts)),
                            ("component", list(self.components)), ("configuration", list(self.configurations))):  # fmt: skip
            for name in names:
                check_name(name, what)
                if name in seen:
                    raise ValueError(f"{name!r} names both a {seen[name]} and a {what}: names must be unique")
                seen[name] = what
        if self.ground is not None and InstancePath.parse(self.ground).head.name not in {*self.parts, *self.components}:
            raise ValueError(f"ground {self.ground!r} is not a part or component of this design")
        by_name = {p.name: p for p in self.parameters}
        for config, values in self.configurations.items():
            for key, value in values.items():
                if key not in by_name:
                    raise ValueError(f"configuration {config!r} sets {key!r}, which is not a parameter")
                by_name[key].coerce(value)
        files: dict[str, set[str]] = {}
        for part in self.parts.values():
            files.setdefault(part.file, set()).add(part.backend or self.backend)
        for file, backends in files.items():
            if len(backends) > 1:
                raise ValueError(f"parts built by {file} name different backends ({', '.join(sorted(backends))})")
        return self

    def values(self, configuration: str | None = None) -> dict[str, Value]:
        """Parameter values of a configuration (the default: `parameters` as written)."""
        values = {p.name: p.value for p in self.parameters}
        if configuration is not None:
            if configuration not in self.configurations:
                raise ValueError(
                    f"no configuration {configuration!r}; configurations: {', '.join(self.configurations) or 'none'}"
                )
            by_name = {p.name: p for p in self.parameters}
            values |= {k: by_name[k].coerce(v) for k, v in self.configurations[configuration].items()}
        return values

    def part_backend(self, part: str) -> str:
        return self.parts[part].backend or self.backend

    def files(self) -> dict[str, list[str]]:
        """Part file -> the parts it builds, in declaration order."""
        out: dict[str, list[str]] = {}
        for name, part in self.parts.items():
            out.setdefault(part.file, []).append(name)
        return out

    def process(self, part: str) -> Manufacturing | None:
        spec = self.parts[part]
        return None if spec.catalog else spec.manufacturing or self.manufacturing
