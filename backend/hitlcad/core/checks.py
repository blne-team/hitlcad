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
    faces: dict[str, list[int]] = Field(default_factory=dict, description="Faces of the part's mesh as it lies when "
                                        "made, by how a view shades them: 'support' (prints on nothing but support), "
                                        "'bed' (lies on the bed)")  # fmt: skip


class Process(Model):
    """How a part is made. Every build checks each made part for its process, as it lies when it is made
    (`made`)."""

    example: ClassVar[dict[str, Any]]
    #: What its checks find, by their names (`ProcessFinding.name`): a title and the usual fix of each.
    findings: ClassVar[dict[str, tuple[str, str]]]

    @abstractmethod
    def checks(self, body: Body) -> list[ProcessFinding]:
        """What it finds of the part as it lies when made (`body`). A printed process's are those that do not depend on
        which way up it prints; those that do are its `way`'s."""

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
    findings: list[ProcessFinding] = Field(
        description="Its process checks, lying as it prints, with the faces each shades"
    )


class PrintSetting(Model):
    """One setting of a printed part, as its print plan gives it."""

    name: str = Field(description="The process's field: 'layer_mm', 'walls', 'infill_percent', 'supports', 'brim'")
    value: float | bool
    text: str = Field(description="As a person reads it, in the process's words: '0.2 mm layers', 'no supports'")
    decided: bool = Field(description="Set in the part's manufacturing (the design's own), else recommended")
    why: str


#: Below this, two ways up need the same support: about what the facets of a curved surface differ by, mm².
SUPPORT_RESOLUTION_MM2 = 10.0


class RoundInterface(Model):
    """A round feature of a printed part that another part fits on or in (a mate's fit): what must come out round as
    it prints, within its fit."""

    name: str = Field(description="Its connector and frame: 'bore[0]'")
    round: Literal["hole", "shaft"]
    diameter: float
    origin: tuple[float, float, float] = Field(description="Its frame's origin, where the part lies")
    axis: tuple[float, float, float] = Field(description="Its frame's Z, where the part lies")
    clearance_mm: tuple[float, float] = Field(description="Its fit's least and greatest diametral clearance")


class PrintWay(Model):
    """What a printed part's process finds of it lying one way up (`PrintedProcess.way`)."""

    findings: list[ProcessFinding] = Field(description="Its checks that depend on how it lies, each with the faces "
                                           "it shades: 'support' (what prints on nothing but support), 'bed'")  # fmt: skip
    stands: bool = Field(description="It stays put on the bed as it prints, with the brim it is given: a way it does "
                         "not is a way to print it only where no other stands")  # fmt: skip
    harmed: list[str] = Field(default_factory=list, description="Its round features with a fit that would not come out round within it "
                              "lying this way")  # fmt: skip


class PrintOrientation(Model):
    """One way a printed part can lie on the bed (its own, or a stable pose of its hull), as its process judges it."""

    up: tuple[float, float, float] = Field(description="The direction of the part's own frame that points up")
    label: str = Field(description="Which way up, as a person reads it: 'as modelled', 'on its +Y face'")
    fits: bool | None = Field(description="On the project's printer; none without one")
    stands: bool = Field(description="It stays put on the bed as it prints (`PrintWay.stands`)")
    harmed: list[str] = Field(
        description="Its round features with a fit that would not come out round within it (`PrintWay.harmed`)"
    )
    support_mm2: float = Field(description="What prints on nothing but support, as its process's checks find it "
                               "(bridged and held edges excluded)")  # fmt: skip
    bed_mm2: float = Field(description="What lies on the bed")
    shown_support_mm2: float = Field(description="Of what prints on support, what shows once the product is "
                                     "assembled (`Context.shown`): scarred where the support comes off")  # fmt: skip
    shown_bed_mm2: float = Field(description="Of what lies on the bed, what shows: it takes the bed's texture")
    height_mm: float
    footprint_mm: tuple[float, float] = Field(description="Its length and width seen from above: the least rectangle "
                                              "round it")  # fmt: skip
    turn_deg: float | None = Field(description="The least turn about Z that fits it on the project's printer's bed; "
                                   "none without a printer, or where none does")  # fmt: skip
    problems: list[str] = Field(description="What its process's checks find wrong with it lying this way")
    current: bool = Field(description="The way it prints now (`up`)")
    better: bool = Field(default=False, description="Better than the way it prints now (`merit`)")

    @property
    def merit(self) -> tuple[bool, bool, int, int, int]:
        """What makes one way up better than another, the first that differs deciding: it fits the printer, it stays
        put on the bed, it keeps more of its fitted round features round, less of what shows prints on support, it
        needs less support (each to `SUPPORT_RESOLUTION_MM2`). Among ways equal in these the lowest is listed first,
        but height alone makes no way better."""
        return (self.fits is False, not self.stands, len(self.harmed),
                round(self.shown_support_mm2 / SUPPORT_RESOLUTION_MM2), round(self.support_mm2 / SUPPORT_RESOLUTION_MM2))  # fmt: skip

    def described(self) -> str:
        """As its plan weighs it."""
        stands = "it stands on the bed" if self.stands else "it does not stay put on the bed"
        oval = f", {', '.join(self.harmed)} not round" if self.harmed else ""
        scarred = f" ({self.shown_support_mm2:.0f} mm² of it shows)" if round(self.shown_support_mm2) else ""
        textured = f" ({self.shown_bed_mm2:.0f} mm² of it shows)" if round(self.shown_bed_mm2) else ""
        return (
            f"{stands}{oval}, {self.support_mm2:.0f} mm² on support{scarred}, {self.bed_mm2:.0f} mm² on the "
            f"bed{textured}, {self.height_mm:.1f} mm tall"
        )


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

    @abstractmethod
    def way(self, body: Body, interfaces: list[RoundInterface]) -> PrintWay:
        """What it finds of the part lying as `body` does that depends on how it lies, its round `interfaces` placed
        so too: the same judgment for the way it prints and for any other it could, so the ways it could lie are
        weighed as it is checked."""

    def recommend(self, facts: PrintFacts) -> list[PrintSetting]:
        """The part's settings: those the design sets, the rest recommended from what the build knows of it."""
        return []

    def made(self) -> fr.Frame:
        """The least turn that points `up` up: none for a part printed as modelled."""
        axis = np.cross(self.up, (0.0, 0.0, 1.0))
        if not axis.any():
            return fr.IDENTITY if self.up[2] > 0 else fr.rotation_about((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 180.0)
        return fr.rotation_about((0.0, 0.0, 0.0), axis, fr.angle_deg(self.up, (0.0, 0.0, 1.0)))
