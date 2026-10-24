"""Plain-text descriptions of designs for coding agents: builds, selection and pins."""

from __future__ import annotations

import json
from pathlib import Path

from hitlcad.core.events import DesignState
from hitlcad.core.types import (
    MANUFACTURING_PROCESSES,
    REQUIREMENT_CHECKS,
    BuildStatus,
    Pin,
    Revision,
    SelectionItem,
    Vec,
)

CHECK_KINDS = ", ".join(c.example["kind"] for c in REQUIREMENT_CHECKS)


def _examples(types: tuple[type, ...], indent: str) -> str:
    """One line per type: its example in design.json and its docstring."""
    return "\n".join(f"{indent}- `{json.dumps(t.example)}`: {' '.join((t.__doc__ or '').split())}" for t in types)


def requirement_checks(indent: str = "") -> str:
    return _examples(REQUIREMENT_CHECKS, indent)


def manufacturing_processes(indent: str = "") -> str:
    return _examples(MANUFACTURING_PROCESSES, indent)


def _v(v: Vec) -> str:
    return "(" + ", ".join(f"{c:.2f}" for c in v) + ")"


MARK = {"pass": "✓", "fail": "✗", "unverified": "?"}


def revision_text(rev: Revision) -> str:
    r = rev.report
    lines = [
        f"Revision {rev.id} ({rev.spec.backend}, {rev.origin.value}) — {r.status.value.upper()} in {r.duration_s:.1f}s"
    ]
    if rev.summary:
        lines.append(rev.summary)
    if r.status == BuildStatus.FAILED:
        lines.append(f"Error:\n{r.error}")
        if r.log_tail:
            lines.append(f"Log tail:\n{r.log_tail}")
    if m := r.metrics:
        lines.append(
            f"Bounding box: X {m.size[0]:.2f} × Y {m.size[1]:.2f} × Z {m.size[2]:.2f} mm "
            f"(min {list(m.bbox_min)}, max {list(m.bbox_max)})"
        )
        vol = f"volume {m.volume:.2f} mm³" if m.volume is not None else "volume unknown (open mesh)"
        tight = "watertight" if m.watertight else "NOT watertight"
        lines.append(
            f"{vol} · surface {m.surface_area:.2f} mm² · {m.triangles} triangles · {m.bodies} bodies · {tight}"
        )
    if r.parts:
        lines.append(f"Parts ({len(r.parts)}): " + "; ".join(
            f"{p.name} {p.size[0]:.1f} × {p.size[1]:.1f} × {p.size[2]:.1f} mm"
            + (f", {p.volume:.0f} mm³" if p.volume is not None else "")
            for p in r.parts))  # fmt: skip
    failing = [c for c in r.checks if not c.passed]
    if r.checks:
        lines.append(f"Checks: {len(r.checks) - len(failing)}/{len(r.checks)} passed"
                     + "".join(f"\n  ✗ {c.name} [{c.severity}]: {c.detail}" for c in failing))  # fmt: skip
    if r.requirements:
        counts = {s: sum(1 for q in r.requirements if q.status == s) for s in ("pass", "fail", "unverified")}
        lines.append(f"Requirements: {counts['pass']} pass, {counts['fail']} fail, {counts['unverified']} unverified"
                     + "".join(f"\n  {MARK[q.status]} {q.text}: {q.detail}" for q in r.requirements))  # fmt: skip
    lines.append("Parameters: " + json.dumps({p.name: p.value for p in rev.spec.parameters}))
    if r.artifacts:
        lines.append("Files: " + ", ".join(a.name for a in r.artifacts if a.role not in ("log", "params")))
    return "\n".join(lines)


def preview_png(rev: Revision, revision_dir: Path) -> bytes | None:
    preview = rev.report.artifact("preview")
    path = revision_dir / preview.path if preview else None
    return path.read_bytes() if path and path.is_file() else None


PREVIEW_CAPTION = "Preview: iso, front (looking +Y), right (looking -X), top (looking -Z)."


def _not_found(revision: int | None, latest: int | None) -> str:
    """Selections and pins move to each new build when their spot is found in it; say when it was not."""
    return f"; not found in revision {latest}, the latest build" if revision and latest and revision != latest else ""


def selection_lines(items: list[SelectionItem], latest: int | None) -> list[str]:
    return [f"{i}. {item.label}" + (f" (revision {item.revision}{_not_found(item.revision, latest)})" if item.revision else "")
            for i, item in enumerate(items, 1)]  # fmt: skip


def pin_lines(pins: list[Pin], latest: int | None) -> list[str]:
    lines = []
    for pin in pins:
        where = pin.label if " at (" in pin.label else f"at {_v(pin.point)}" + (f" on {pin.label}" if pin.label else "")
        rev = f", revision {pin.revision}{_not_found(pin.revision, latest)}" if pin.revision else ""
        lines.append(f"- {pin.id} {where}{rev}: {pin.note.strip() or '(no note: the location itself is the point)'}")
    return lines


def feedback_text(state: DesignState) -> str:
    """What the human is pointing at in the panel: the current selection and the open pins."""
    parts = []
    latest = state.latest_successful_revision.id if state.latest_successful_revision else None
    if state.selection:
        parts.append("Selected in the 3D view:\n" + "\n".join(selection_lines(state.selection, latest)))
    if pins := state.open_pins:
        parts.append(
            "Open pins (resolve them with resolve_pins once addressed):\n" + "\n".join(pin_lines(pins, latest))
        )
    return "\n\n".join(parts) or "Nothing is selected and there are no open pins."


def pointing_text(state: DesignState) -> str:
    """One design's selection and open pins, headed by the design's name, folder and latest revision."""
    rev = state.current_revision
    head = f"Design {state.name!r} at {state.path}" + (f" (latest revision {rev.id})" if rev else "")
    return head + "\n" + feedback_text(state)


def design_text(state: DesignState) -> str:
    rev = state.current_revision
    lines = [f"Design {state.name!r} ({state.backend}) at {state.path}"]
    if state.building:
        lines.append("A build is running.")
    if state.error:
        lines.append(f"Problem: {state.error}")
    lines.append(f"Brief: {state.brief.title}" if state.brief else "No brief in design.json yet.")
    lines.append(("Latest revision:\n" + revision_text(rev)) if rev else "Not built yet.")
    if state.latest_successful_revision:
        lines.append(f"Outputs of the latest successful build: {state.path}/build/")
    lines.append(feedback_text(state))
    return "\n\n".join(lines)
