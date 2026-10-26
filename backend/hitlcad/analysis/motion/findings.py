"""What a sweep finds, whichever way it moves its parts: the first contacts each way, the least gaps the clearance
requirements watch, and the pairs it leaves out because they overlap where they sit or cannot be measured."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import Field

from hitlcad.analysis.base import ResultCache
from hitlcad.analysis.interference import beyond, overlap
from hitlcad.analysis.model import AnalysisSettings, Leaf
from hitlcad.analysis.motion.plans import World
from hitlcad.core import frames as fr
from hitlcad.kernel.errors import Unmeasurable
from hitlcad.kernel.model import Model


class Contact(Model):
    """The first position, moving from where the mate sits towards `end`, at which two instances overlap."""

    kind: str
    at: float
    end: float
    clear: float = Field(description="The last position checked clear before it")
    a: str
    b: str
    overlap_mm3: float


class Stop(Model):
    """Where a direction was given up: two instances stay too close for the positions left."""

    kind: str
    at: float
    a: str
    b: str
    gap_mm: float


class Least(Model):
    """The least gap a pair a clearance requirement names comes to, and where: `at` None is somewhere in a
    full turn (a pair cleared at once, see the module's docstring)."""

    a: str
    b: str
    gap_mm: float
    kind: str
    at: float | None
    complete: bool = Field(description="Every range was swept to its ends")


class Findings(Model):
    """What a sweep found that either kind of sweep finds."""

    contacts: list[Contact] = Field(default_factory=list)
    resting: list[tuple[str, str, float]] = Field(default_factory=list, description="Pairs overlapping where they sit")
    unmeasurable: list[tuple[str, str, str]] = Field(default_factory=list)
    least: list[Least] = Field(default_factory=list)


@dataclass
class Pair:
    """A leaf a sweep moves and another its motion may bring it near, the gap a clearance requirement needs them to
    keep (`tracked`: one names them), and what they may share where they sit, as a mate allows: more than that is
    a contact."""

    p: Leaf
    q: Leaf
    need: float
    tracked: bool
    sitting_mm3: float = field(default=0.0, kw_only=True)


P = TypeVar("P", bound=Pair)


def at_rest(pairs: list[P], world: World, settings: AnalysisSettings, cache: ResultCache, out: Findings) -> list[P]:
    """The pairs to sweep: not those overlapping where they sit beyond what a mate allows (interference reports
    them, into `out.resting`), nor those that cannot be measured (`out.unmeasurable`). Each kept pair learns what
    it may share where it sits."""
    keep = []
    for pair in pairs:
        try:
            shared = overlap(cache, pair.p, fr.IDENTITY, pair.q, fr.IDENTITY)
        except Unmeasurable as e:
            out.unmeasurable.append((pair.p.path, pair.q.path, str(e)))
            continue
        allowance = world.ok_pairs.get(frozenset((pair.p.path, pair.q.path)))
        if shared <= settings.overlap_mm3 or (
            allowance and not beyond(allowance, pair.p, pair.q, shared, settings, world.tolerances)
        ):
            pair.sitting_mm3 = shared if shared > settings.overlap_mm3 else 0.0
            keep.append(pair)
        else:
            out.resting.append((pair.p.path, pair.q.path, round(shared, 6)))
    return keep
