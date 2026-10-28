"""What a requirement check and a manufacturing process are (`Check`, `Process`): each a model that says what it
means (docstring, example, the usual fix) and verifies or checks itself on what the build measured. The checks and
processes themselves are plugins' members; hitlcad's own are in `hitlcad.builtin`.

Checks target an address: a part (measured in its own frame, the way it is made), an instance or
component path (measured where it is placed), or the whole design when no target is given.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field, model_validator

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
    """How a part is made. Every build checks each made part for its process, in the part's own frame
    (the way it is made)."""

    example: ClassVar[dict[str, Any]]
    #: What its checks find, by their names (`ProcessFinding.name`): a title and the usual fix of each.
    findings: ClassVar[dict[str, tuple[str, str]]]

    @abstractmethod
    def checks(self, body: Body) -> list[ProcessFinding]: ...

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        """How much the process makes a round feature's diameter smaller (hole) or larger (shaft) than modelled, mm."""
        return 0.0


class PrintedProcess(Process):
    """A printing process: printed holes come out smaller, and shafts larger, than modelled."""

    hole_allowance_mm: float = Field(default=0.0, ge=0)
    shaft_allowance_mm: float = Field(default=0.0, ge=0)

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        return self.hole_allowance_mm if feature == "hole" else self.shaft_allowance_mm
