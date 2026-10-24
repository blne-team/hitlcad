"""The design graph of a design for agents and the panel, and comparisons between revisions."""

from __future__ import annotations

import difflib
from pathlib import Path

from hitlcad.config import Settings
from hitlcad.core.report import Revision
from hitlcad.designs.folder import DESIGN_FILE, DesignFolder
from hitlcad.graph import Context, DesignGraph, revision_summary
from hitlcad.service.designs import DesignService, NotFoundError


class ExploreService:
    def __init__(self, designs: DesignService, settings: Settings) -> None:
        self.designs = designs
        self.settings = settings

    def graph(self, design_id: str, configuration: str | None = None) -> DesignGraph:
        state = self.designs.get(design_id)
        folder = DesignFolder(Path(state.path))
        try:
            design = folder.load()
        except (OSError, ValueError):
            design = None
        revision = state.latest(configuration)
        stale = self._stale(folder, revision) if revision else []
        used_by = [self.designs.get(i).name for i in self.designs.where_used(design_id)]
        return DesignGraph(Context(state=state, design=design, revision=revision, stale=stale, used_by=used_by,
                                   limit=self.settings.explore_limit))  # fmt: skip

    @staticmethod
    def _stale(folder: DesignFolder, revision: Revision) -> list[str]:
        """Files that changed since `revision` was built: what its facts no longer describe."""
        try:
            now = folder.sources()
            design = folder.load()
            assets = folder.assets()
        except (OSError, ValueError):
            return [DESIGN_FILE]
        then = revision.spec.sources
        changed = sorted(f for f in {*now, *then} if now.get(f) != then.get(f))
        if design.model_dump(exclude={"brief"}) != revision.spec.design.model_dump(exclude={"brief"}):
            changed.insert(0, DESIGN_FILE)
        changed += sorted(f for f in {*assets, *revision.spec.assets} if assets.get(f) != revision.spec.assets.get(f))
        return changed

    def compare(self, design_id: str, base: int, target: int | None = None) -> str:
        """What changed between two revisions: parameters, files, results, parts rebuilt, size and mass."""
        state = self.designs.get(design_id)
        a = state.revision(base)
        b = state.revision(target) if target else state.current_revision
        if a is None or b is None:
            raise NotFoundError(f"revision {base if a is None else target} not found")
        lines = [f"Revision {a.id} → {b.id}", revision_summary(b, limit=self.settings.explore_limit)]
        va, vb = a.report.values, b.report.values
        if changed := [f"{k}: {va.get(k)} → {vb.get(k)}" for k in sorted({*va, *vb}) if va.get(k) != vb.get(k)]:
            lines.append("Parameters: " + "; ".join(changed))
        if a.spec.configuration != b.spec.configuration:
            lines.append(f"Configuration: {a.spec.configuration or 'default'} → {b.spec.configuration or 'default'}")
        sa, sb = a.spec.sources, b.spec.sources
        for f in sorted({*sa, *sb}):
            if sa.get(f) != sb.get(f):
                diff = difflib.unified_diff((sa.get(f) or "").splitlines(), (sb.get(f) or "").splitlines(),
                                            f"rev {a.id}/{f}", f"rev {b.id}/{f}", lineterm="", n=2)  # fmt: skip
                lines.append("\n".join(diff))
        if a.spec.design.model_dump(exclude={"brief", "parameters"}) != b.spec.design.model_dump(
            exclude={"brief", "parameters"}
        ):
            da = a.spec.design.model_dump_json(indent=1, exclude={"brief", "parameters"}).splitlines()
            db = b.spec.design.model_dump_json(indent=1, exclude={"brief", "parameters"}).splitlines()
            lines.append("\n".join(difflib.unified_diff(da, db, f"rev {a.id}/{DESIGN_FILE}", f"rev {b.id}/{DESIGN_FILE}",
                                                        lineterm="", n=1)))  # fmt: skip
        ra = {(r.analysis, r.subject, tuple(r.about)): r for r in a.report.results}
        rb = {(r.analysis, r.subject, tuple(r.about)): r for r in b.report.results}
        moved = [f"  {r.analysis} {r.subject}: {ra[k].status} → {r.status}: {r.detail}"
                 for k, r in rb.items() if k in ra and ra[k].status != r.status]  # fmt: skip
        new = [
            f"  new {r.status}: {r.analysis} {r.subject}: {r.detail}"
            for k, r in rb.items()
            if k not in ra and r.status != "pass"
        ]
        gone = [f"  gone: {r.analysis} {r.subject}" for k, r in ra.items() if k not in rb and r.status != "pass"]
        if moved or new or gone:
            lines.append("Results that changed:\n" + "\n".join([*moved, *new, *gone][: self.settings.explore_limit]))
        rebuilt = [p.name for p in b.report.parts if not p.cached]
        if rebuilt:
            lines.append(f"Parts rebuilt in {b.id}: {', '.join(rebuilt)}")
        ma, mb = a.report.metrics, b.report.metrics
        if ma and mb and ma.size != mb.size:
            lines.append(
                "Size: "
                + " × ".join(f"{x:.2f}" for x in ma.size)
                + " → "
                + " × ".join(f"{x:.2f}" for x in mb.size)
                + " mm"
            )
        if a.report.mass and b.report.mass and abs(a.report.mass.mass_g - b.report.mass.mass_g) > 1e-6:
            lines.append(f"Mass: {a.report.mass.mass_g:.1f} → {b.report.mass.mass_g:.1f} g")
        return "\n\n".join(lines)
