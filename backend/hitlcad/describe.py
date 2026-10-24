"""Plain-text descriptions for coding agents: what the human points at, and the guides generated from the
framework's unions (requirement checks, processes, connector kinds, mate and relation types)."""

from __future__ import annotations

import json

from hitlcad.core.checks import MANUFACTURING_PROCESSES, REQUIREMENT_CHECKS
from hitlcad.core.events import DesignState
from hitlcad.core.structure import CONNECTOR_KINDS, MATE_TYPES, RELATION_TYPES, FastenerSet
from hitlcad.core.types import Pin, SelectionItem, Vec

CHECK_KINDS = ", ".join(c.example["kind"] for c in REQUIREMENT_CHECKS)


def _examples(types: tuple[type, ...], indent: str) -> str:
    """One line per type: its example in design.json and its docstring."""
    return "\n".join(f"{indent}- `{json.dumps(t.example)}`: {' '.join((t.__doc__ or '').split())}" for t in types)


def guide(indent: str = "") -> dict[str, str]:
    """The skill's generated sections."""
    return {
        "checks": _examples(REQUIREMENT_CHECKS, indent),
        "processes": _examples(MANUFACTURING_PROCESSES, indent),
        "connectors": _examples(CONNECTOR_KINDS, indent),
        "mates": _examples(MATE_TYPES, indent),
        "relations": _examples(RELATION_TYPES, indent),
        "fasteners": _examples((FastenerSet,), indent),
    }


def _v(v: Vec) -> str:
    return "(" + ", ".join(f"{c:.2f}" for c in v) + ")"


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
    latest = state.latest(None).id if state.latest(None) else None
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


PREVIEW_CAPTION = "Preview: iso, front (looking +Y), right (looking -X), top (looking -Z)."
