"""The design graph of a design for agents and the panel, and comparisons between revisions."""

from __future__ import annotations

import difflib

from hitlcad.agent.graph import Context, DesignGraph, revision_summary
from hitlcad.config import Settings
from hitlcad.core.design import DESIGN_FILE
from hitlcad.core.report import Revision
from hitlcad.kernel.errors import NOT_FOUND_REVISION, HitlcadError
from hitlcad.services.designs import DesignService
from hitlcad.workspace.document import Snapshot
from hitlcad.workspace.folder import load_design


class ExploreService:
    def __init__(self, designs: DesignService, settings: Settings) -> None:
        self.designs = designs
        self.settings = settings

    def graph(self, design_id: str, configuration: str | None = None) -> DesignGraph:
        state = self.designs.get(design_id)
        files = self.designs.files(design_id)
        design, design_error = None, None
        try:
            design = load_design(files.design)
        except HitlcadError as e:
            design_error = str(e)
        latest = state.latest(configuration)
        revision = self.designs.revision(design_id, latest.id) if latest else None
        stale = self._stale(files, revision) if revision else []
        used_by = [self.designs.get(i).name for i in self.designs.where_used(design_id)]
        return DesignGraph(Context(state=state, design=design, design_error=design_error, revision=revision,
                                   stale=stale, used_by=used_by, limit=self.settings.explore_limit,
                                   load=lambda rev: self.designs.revision(design_id, rev)))  # fmt: skip

    @staticmethod
    def _stale(files: Snapshot, revision: Revision) -> list[str]:
        """Files that changed since `revision` was built: what its facts no longer describe."""
        assert revision.spec is not None  # a revision that built
        try:
            design = load_design(files.design)
        except HitlcadError:
            return [DESIGN_FILE]
        now, assets = files.sources(), files.vendor()
        then = revision.spec.sources
        changed = sorted(f for f in {*now, *then} if now.get(f) != then.get(f))
        if design.model_dump(exclude={"brief"}) != revision.spec.design.model_dump(exclude={"brief"}):
            changed.insert(0, DESIGN_FILE)
        changed += sorted(f for f in {*assets, *revision.spec.assets} if assets.get(f) != revision.spec.assets.get(f))
        return changed

    def compare(self, design_id: str, base: int, target: int | None = None) -> str:
        """What changed between two revisions: parameters, files, results, parts rebuilt, size and mass."""
        state = self.designs.get(design_id)
        last = state.current_revision
        if last is None:
            raise NOT_FOUND_REVISION("there is no revision yet")
        a, b = self.designs.revision(design_id, base), self.designs.revision(design_id, target or last.id)
        lines = [f"Revision {a.id} → {b.id}", revision_summary(b, limit=self.settings.explore_limit)]
        va, vb = a.report.values, b.report.values
        if changed := [f"{k}: {va.get(k)} → {vb.get(k)}" for k in sorted({*va, *vb}) if va.get(k) != vb.get(k)]:
            lines.append("Parameters: " + "; ".join(changed))
        if a.configuration != b.configuration:
            lines.append(f"Configuration: {a.configuration or 'default'} → {b.configuration or 'default'}")
        sa, sb = (r.spec.sources if r.spec else {} for r in (a, b))
        for f in sorted({*sa, *sb}):
            if sa.get(f) != sb.get(f):
                diff = difflib.unified_diff((sa.get(f) or "").splitlines(), (sb.get(f) or "").splitlines(),
                                            f"rev {a.id}/{f}", f"rev {b.id}/{f}", lineterm="", n=2)  # fmt: skip
                lines.append("\n".join(diff))
        da, db = (r.spec.design.model_dump_json(indent=1, exclude={"brief", "parameters"}).splitlines() if r.spec else []
                  for r in (a, b))  # fmt: skip
        if da != db:
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
