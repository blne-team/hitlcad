"""What the human is pointing at across designs, as text for an agent: added to each prompt by the
agents' hitlcad hook, or copied by the human to paste into any chat."""

from __future__ import annotations

from pathlib import Path

from hitlcad.agent import describe
from hitlcad.core.events import FeedbackDelivered
from hitlcad.services.designs import DesignService

HEADER = "The human is pointing at these in the hitlcad panel (model space, mm, Z up):"


class ContextFeed:
    def __init__(self, designs: DesignService) -> None:
        self.designs = designs

    def read(self, root: str | Path | None = None) -> str:
        """Selections and open pins of the designs under ``root`` (all designs without it)."""
        sections = [describe.pointing_text(s) for s in self.designs.under(root) if s.selection or s.open_pins]
        return "\n\n".join([HEADER, *sections]) if sections else ""

    def deliver(self, root: str | Path, agent: str, session: str) -> str:
        """Context for the next prompt of an agent session. A design whose selection or open pins
        changed since the session last got them is given in full (and the delivery recorded, so the
        panel shows it); unchanged ones get a one-line reminder, so they survive the agent
        summarising its history; one cleared since then is reported as cleared."""
        full, unchanged, cleared = [], [], []
        for state in self.designs.under(root):
            selection, pins = state.pointing()
            last = state.deliveries.get(session)
            if last is not None and (last.selection, last.pins) == (selection, pins):
                if selection or pins:
                    unchanged.append(f"- Design {state.name!r} at {state.path}: {len(selection)} selected, "
                                     f"open pins {', '.join(pins) or 'none'}")  # fmt: skip
                continue
            if not selection and not pins and (last is None or not (last.selection or last.pins)):
                continue  # nothing to say, and nothing was said before
            self.designs.emit(state.id, FeedbackDelivered(agent=agent, session=session, selection=selection, pins=pins))
            if selection or pins:
                full.append(describe.pointing_text(state))
            else:
                cleared.append(f"- Design {state.name!r} at {state.path}")
        parts = [HEADER, *full] if full else []
        if cleared:
            parts.append("Nothing is selected or pinned any more in:\n" + "\n".join(cleared))
        if unchanged:
            parts.append("Still pointed at, unchanged since you were last told (explore_design(path) repeats the "
                         "details):\n" + "\n".join(unchanged))  # fmt: skip
        return "\n\n".join(parts)
