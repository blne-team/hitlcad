"""The Analysis extension point.

An analysis reads a built design and returns `Result`s, each naming the kind of check it is (its code, from the
error catalog) and the addresses it is about. The pipeline runs every analysis in `default_analyses` on every
build. Costly results are cached by the inputs they depend on and the code that computes them (`Context.cache`),
so a rebuild only recomputes what changed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from hitlcad.analysis.model import AnalysisSettings, BuildBodies, BuiltDesign, PlacedFrame
from hitlcad.core import frames as fr
from hitlcad.core.report import Result, Severity, Status
from hitlcad.kernel.errors import Diagnostic, ErrorKind, StructureError


@cache
def analysis_code() -> str:
    """The analyses, the domain model and geometry they apply and hitlcad's own plugins, by content: what any result
    computed from the same inputs also depends on."""
    root = Path(__file__).parents[1]
    packages = ("analysis", "builtin", "core", "geometry", "kernel")
    files = sorted(p for package in packages for p in (root / package).rglob("*.py"))
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


@cache
def member_code(module: str) -> str:
    """An installed plugin's code by content, for results its members compute; hitlcad's own is `analysis_code`."""
    top = module.partition(".")[0]
    spec = importlib.util.find_spec(top)
    if top == "hitlcad" or spec is None or spec.origin is None:
        return ""
    files = sorted(Path(spec.origin).parent.rglob("*.py"))
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


class ResultCache:
    """Results of costly checks by the hash of what they depend on and of hitlcad's code (`analysis_code`), shared
    across builds."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps([analysis_code(), *parts], sort_keys=True, default=str).encode()).hexdigest()

    def get_or(self, key: str, compute: Callable[[], Any]) -> Any:
        path = self.root / key[:2] / f"{key}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        value = compute()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return value


@dataclass
class Context:
    built: BuiltDesign
    settings: AnalysisSettings
    bodies: BuildBodies
    cache: ResultCache
    watched: Mapping[str, int] = field(default_factory=dict)  # loop sweeps the human watched: key -> revision

    @property
    def tol(self) -> Any:
        return self.built.tolerances


#: When an analysis runs: cause before effect. Where results share a cause (`Result.cause`), the earliest
#: analysis's lead the problem.
STAGES = ("structure", "parts", "connectors", "joints", "assembly", "motion", "process", "requirements")


class Analysis(Protocol):
    name: str  # what its results are filed under (`Result.analysis`)
    stage: str  # one of STAGES
    title: str  # for the panel: "Fastener sets"
    summary: str  # one line for the panel: what it checks

    def run(self, ctx: Context) -> list[Result]: ...


def result(analysis: str, kind: ErrorKind, subject: str, status: Status | bool | None, detail: str,
           about: Iterable[str] = (), values: dict[str, Any] | None = None, severity: Severity = "error",
           cause: str = "") -> Result:  # fmt: skip
    if not isinstance(status, str):
        status = "unverified" if status is None else "pass" if status else "fail"
    clean = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in (values or {}).items()}
    return Result(analysis=analysis, code=kind.code, subject=subject, status=status, severity=severity, detail=detail,
                  about=sorted(set(about)), values=clean, cause=cause)  # fmt: skip


def reported(analysis: str, d: Diagnostic, subject: str = "", about: Iterable[str] = (),
             status: Status | bool | None = False) -> Result:  # fmt: skip
    """A diagnostic as a result: what an analysis or the build met, where it is in the design's files."""
    where = d.where
    subject = subject or (where.address or where.file or "" if where else "") or d.code
    if not isinstance(status, str):
        status = "unverified" if status is None else "pass" if status else "fail"
    return Result(analysis=analysis, code=d.code, subject=subject, status=status, detail=d.message, where=where,
                  about=sorted(set(about)))  # fmt: skip


def structural(subject: str, error: StructureError, about: Iterable[str] = ()) -> Result:
    return reported("structure", error.diagnostic, subject, about)


def member_failed(analysis: str, member: Any, what: str, e: Exception, about: Iterable[str] = (),
                  subject: str = "") -> Result:  # fmt: skip
    """A design model member's own code raising (a check's, a process's): unverified, with the plugin it is from,
    or a bug in hitlcad when that is hitlcad's own."""
    from hitlcad.kernel.plugins import registry

    logging.getLogger(__name__).exception("%s failed", what)
    found = registry()
    about = list(about)
    return reported(analysis, found.failed(found.origin(member), what, e), subject or (about[0] if about else what),
                    about, status=None)  # fmt: skip


def pair(a: list[PlacedFrame], b: list[PlacedFrame]) -> list[tuple[PlacedFrame, PlacedFrame]]:
    """Frames of two ends paired one-to-one, each with the nearest frame of the other whose axis it shares."""
    if len(a) != len(b):
        raise StructureError(f"{len(a)} frames on one end and {len(b)} on the other; a mate pairs them one-to-one")
    cost = np.array([[fr.distance_to_line(fr.origin(fb.frame), fr.origin(fa.frame), fr.z_axis(fa.frame))
                      + 1e-3 * float(np.linalg.norm(fr.origin(fb.frame) - fr.origin(fa.frame))) for fb in b] for fa in a])  # fmt: skip
    pairs, used_a, used_b = [], set(), set()
    for i, j in sorted(np.ndindex(cost.shape), key=lambda ij: cost[ij]):
        if i not in used_a and j not in used_b:
            pairs.append((a[i], b[j]))
            used_a.add(i)
            used_b.add(j)
    return sorted(pairs, key=lambda p: (p[0].instance, p[0].index))


def label(f: PlacedFrame) -> str:
    """'planet[1].bore', or 'carrier.pins[2]' for one frame of a pattern."""
    pattern = len(f.part.frames[f.connector]) > 1
    return f"{f.instance}.{f.connector}" + (f"[{f.index}]" if pattern else "")


def default_analyses() -> list[Analysis]:
    from hitlcad.analysis.checks import Connectors, Manufacturing, Parts, Requirements
    from hitlcad.analysis.coverage import Coverage
    from hitlcad.analysis.fasteners import Fasteners
    from hitlcad.analysis.interference import Interference
    from hitlcad.analysis.mates import Mates
    from hitlcad.analysis.motion import Motion
    from hitlcad.analysis.relations import Relations
    from hitlcad.analysis.structure import Structure
    from hitlcad.kernel.errors import PLUGIN_ERROR
    from hitlcad.kernel.plugins import registry

    own: list[Analysis] = [Structure(), Parts(), Connectors(), Mates(), Relations(), Fasteners(), Coverage(),
                           Interference(), Motion(), Manufacturing(), Requirements()]  # fmt: skip
    found = registry()
    for a in found.analyses:
        if a.stage not in STAGES:
            plugin = found.origin(a)
            found.failures.append(PLUGIN_ERROR.diagnostic(
                f"{plugin.distribution if plugin else 'a plugin'}: analysis {a.name} runs at stage {a.stage!r}, which "
                f"there is not; stages: {', '.join(STAGES)}"))  # fmt: skip
    return sorted([*own, *(a for a in found.analyses if a.stage in STAGES)], key=lambda a: STAGES.index(a.stage))
