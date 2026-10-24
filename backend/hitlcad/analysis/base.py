"""The Analysis extension point.

An analysis reads a built design and returns `Result`s, each naming the addresses it is about.
Analyses are registered in `app.py`; the pipeline runs every one on every build. Costly results are
cached by the inputs they depend on (`Context.cache`), so a rebuild only recomputes what changed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from hitlcad.analysis.model import AnalysisSettings, BuildBodies, BuiltDesign, PlacedFrame, StructureError
from hitlcad.core import frames as fr
from hitlcad.core.report import Result, Severity, Status


class ResultCache:
    """Results of costly checks by the hash of what they depend on, shared across builds."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

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

    @property
    def tol(self) -> Any:
        return self.built.tolerances


class Analysis(Protocol):
    name: str

    def run(self, ctx: Context) -> list[Result]: ...


def result(analysis: str, subject: str, status: Status | bool | None, detail: str, about: Iterable[str] = (),
           values: dict[str, Any] | None = None, severity: Severity = "error") -> Result:  # fmt: skip
    if not isinstance(status, str):
        status = "unverified" if status is None else "pass" if status else "fail"
    clean = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in (values or {}).items()}
    return Result(analysis=analysis, subject=subject, status=status, severity=severity, detail=detail,
                  about=sorted(set(about)), values=clean)  # fmt: skip


def structural(subject: str, error: StructureError, about: Iterable[str] = ()) -> Result:
    return result("structure", subject, False, str(error), about)


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

    return [Structure(), Parts(), Connectors(), Mates(), Relations(), Fasteners(), Coverage(), Interference(), Motion(),
            Manufacturing(), Requirements()]  # fmt: skip
