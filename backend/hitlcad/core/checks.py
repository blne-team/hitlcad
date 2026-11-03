"""What a requirement check and a manufacturing process are (`Check`, `Process`): each a model that says what it
means (docstring, example, the usual fix) and verifies or checks itself on what the build measured. The checks and
processes themselves are plugins' members; hitlcad's own are in `hitlcad.builtin`.

Checks target an address: a part (measured in its own frame), an instance or component path (measured where it is
placed), or the whole design when no target is given. A process checks a part as it lies when it is made
(`Process.made`).
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import numpy as np
from pydantic import Field, field_validator, model_validator

from hitlcad.core import frames as fr
from hitlcad.kernel.model import Model

if TYPE_CHECKING:
    from hitlcad.analysis.model import Body, BuildBodies

Verdict = tuple[bool | None, str]  # (passed, detail); None: this build cannot tell, and the detail says why
#: A requirement's number: as written, or a parameter's name or "interfaces.<name>" that gives it, so a
#: requirement follows the design ("as tall as the height parameter") in every configuration.
Number = float | str

AXES = {"x": 0, "y": 1, "z": 2}


def need(low: float | None, high: float | None) -> str:
    """'≥ 2 and ≤ 5': what bounds ask."""
    return " and ".join(
        p for p in (f"≥ {low:g}" if low is not None else "", f"≤ {high:g}" if high is not None else "") if p
    )


def _bounds(value: float, low: float | None, high: float | None, unit: str) -> Verdict:
    ok = (low is None or value >= low) and (high is None or value <= high)
    return ok, f"{value:.2f} {unit} (needs {need(low, high)})"


class Check(Model):
    """A requirement check: measured on every build. Its docstring and `example` are the agent's guide, and `hint`
    the usual fix when it fails."""

    example: ClassVar[dict[str, Any]]
    hint: ClassVar[str]

    @abstractmethod
    def verify(self, build: BuildBodies) -> Verdict: ...

    def targets(self) -> list[str]:
        return []

    def cause(self, build: BuildBodies) -> str:
        """Why it failed, where another analysis reports that too (`report.Cause`); empty when it is its own."""
        return ""

    def files(self) -> list[str]:
        """The design's files it reads besides its code (a reference picture), relative to its folder: checksummed
        into the build (`BuildSpec.checked`), so changing one builds again."""
        return []

    def figure(self, build: BuildBodies) -> bytes | None:
        """A picture of what it measured (PNG), shown to the agent with a failing result."""
        return None

    def keeps_apart(self) -> tuple[tuple[str, str], Number] | None:
        """The two placed things it keeps at least a gap apart, and that gap: what motion analysis watches as it
        sweeps."""
        return None


class Bounded(Model):
    min: Number | None = None
    max: Number | None = None

    @model_validator(mode="after")
    def _bounded(self) -> Bounded:
        if self.min is None and self.max is None:
            raise ValueError("give min, max or both")
        if isinstance(self.min, float) and isinstance(self.max, float) and self.min > self.max:
            raise ValueError("min is greater than max")
        return self

    def bounds(self, value: float, build: BuildBodies, unit: str) -> Verdict:
        return _bounds(value, build.number(self.min), build.number(self.max), unit)


class OnTarget(Check):
    target: str | None = Field(
        default=None,
        description="A part (its own frame), an instance or component path (placed); default: the whole design",
    )

    def subject(self, build: BuildBodies) -> Body:
        return build.subject(self.target)

    def targets(self) -> list[str]:
        return [self.target] if self.target else []


# --------------------------------------------------------------------------- manufacturing


class ProcessFinding(Model):
    name: str
    passed: bool | None
    detail: str


class Process(Model):
    """How a part is made. Every build checks each made part for its process, as it lies when it is made
    (`made`)."""

    example: ClassVar[dict[str, Any]]
    #: What its checks find, by their names (`ProcessFinding.name`): a title and the usual fix of each.
    findings: ClassVar[dict[str, tuple[str, str]]]

    @abstractmethod
    def checks(self, body: Body) -> list[ProcessFinding]: ...

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        """How much the process makes a round feature's diameter smaller (hole) or larger (shaft) than modelled, mm."""
        return 0.0

    def made(self) -> fr.Frame:
        """How the part lies as it is made, from its own frame: as it is modelled, +Z up."""
        return fr.IDENTITY


class PrintFacts(Model):
    """What a build knows of a printed part, for its process to recommend its settings by (`recommend`)."""

    nozzle_mm: float | None = Field(description="The project's printer's; none without one (the process's own)")
    tightest_fit_mm: float | None = Field(description="The least diametral clearance of its fits (negative: "
                                          "interference); none without a fit")  # fmt: skip
    threaded_into: list[str] = Field(description="The fastener sets whose screws thread into it")
    findings: list[ProcessFinding] = Field(description="Its process checks, lying as it prints")


class PrintSetting(Model):
    """One setting of a printed part, as its print plan gives it."""

    name: str = Field(description="The process's field: 'layer_mm', 'walls', 'infill_percent', 'supports', 'brim'")
    value: float | bool
    decided: bool = Field(description="Set in the part's manufacturing (the design's own), else recommended")
    why: str


class PrintedProcess(Process):
    """A printing process: printed holes come out smaller, and shafts larger, than modelled. A part is modelled
    for its assembly, and prints the way `up` turns it. Its settings left unset are recommended (`recommend`);
    those set are the design's, with their reason in `why`."""

    hole_allowance_mm: float = Field(default=0.0, ge=0)
    shaft_allowance_mm: float = Field(default=0.0, ge=0)
    up: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 1.0), description="The direction of the part's own frame that points up as it prints: "
        "[0, 0, 1] prints it as modelled, [0, -1, 0] stands it on its +Y face",
    )  # fmt: skip

    why: str = Field(default="", description="Why its settings are what they are, where the design sets them")

    @field_validator("up")
    @classmethod
    def _direction(cls, up: tuple[float, float, float]) -> tuple[float, float, float]:
        if not any(up):
            raise ValueError("up is a direction, so not [0, 0, 0]")
        return up

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        return self.hole_allowance_mm if feature == "hole" else self.shaft_allowance_mm

    def recommend(self, facts: PrintFacts) -> list[PrintSetting]:
        """The part's settings: those the design sets, the rest recommended from what the build knows of it."""
        return []

    def made(self) -> fr.Frame:
        """The least turn that points `up` up: none for a part printed as modelled."""
        axis = np.cross(self.up, (0.0, 0.0, 1.0))
        if not axis.any():
            return fr.IDENTITY if self.up[2] > 0 else fr.rotation_about((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 180.0)
        return fr.rotation_about((0.0, 0.0, 0.0), axis, fr.angle_deg(self.up, (0.0, 0.0, 1.0)))
