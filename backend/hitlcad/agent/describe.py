"""Plain-text descriptions for coding agents: what the human points at, the guides generated from the
framework's unions (requirement checks, processes, connector kinds, mate and relation types), and the playbook
generated from the error catalog."""

from __future__ import annotations

import json

import hitlcad.core.design  # noqa: F401  # the design model's members are gathered with its unions
from hitlcad.core.events import DesignState
from hitlcad.core.structure import FastenerSet
from hitlcad.core.types import Pin, SelectionItem, Vec
from hitlcad.kernel.errors import catalog
from hitlcad.kernel.plugins import registry


def _examples(types: tuple[type, ...], indent: str) -> str:
    """One line per type: its example in design.json and its docstring."""
    return "\n".join(f"{indent}- `{json.dumps(t.example)}`: {' '.join((t.__doc__ or '').split())}" for t in types)


def model_package_lines(indent: str = "") -> list[str]:
    """Importable construction helpers, described without importing backend-specific code."""
    return [
        f"{indent}- `{p.package}` ({', '.join(p.backends)})" + (f": {p.summary}" if p.summary else "")
        for p in registry().model_packages
    ]


def guide(indent: str = "") -> dict[str, str]:
    """The skill's generated sections: every installed member, from its docstring and example."""
    found = registry()
    return {
        "checks": _examples(found.checks, indent),
        "processes": _examples(found.processes, indent),
        "connectors": _examples(found.connectors, indent),
        "mates": _examples(found.mates, indent),
        "relations": _examples(found.relations, indent),
        "fasteners": _examples((FastenerSet,), indent),
        "builders": "\n".join(model_package_lines(indent)),
    }


#: The playbook's sections: whose a code is to fix, and what that asks of the agent.
OWNERS = {
    "design": "The design's: change its files",
    "request": "The call's: change the call",
    "environment": "The machine's: tell the human",
    "plugin": "An installed plugin's: tell the human",
    "hitlcad": "hitlcad's: a bug, tell the human and do not change the design to avoid it",
}


def playbook() -> str:
    """Every code a build or a tool answers with, by whose it is to fix: its title and its usual fix."""
    lines = ["# Codes and their usual fixes", "",
             "Every problem `build_design` reports, and every error a tool answers with, names its code: "
             "`✗ mate base.top+lid.bottom [mate.frames]: …`. The answer gives the usual fix the first time a code "
             "appears; here are all of them.", ""]  # fmt: skip
    kinds = sorted(catalog().values(), key=lambda k: k.code)
    for owner, title in OWNERS.items():
        mine = [k for k in kinds if k.owner == owner]
        if mine:
            lines += [f"## {title}", ""]
            lines += [f"- `{k.code}`: {k.title.rstrip('.')}." + (f" {k.hint}" if k.hint else "") + (f" ({k.skill})" if k.skill else "")
                      for k in mine]  # fmt: skip
            lines.append("")
    return "\n".join(lines)


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
        moved = f", {pin.motion.text()}" if pin.motion else ""
        lines.append(
            f"- {pin.id} {where}{rev}{moved}: {pin.note.strip() or '(no note: the location itself is the point)'}"
        )
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
