"""Append-only design events and the projection that folds them into design state.

Each design folder has one event log: what was built, what the human selected and pinned, and
errors. The folder's files stay the source of truth; the log is the review history around them. A revision's
event lists it (`RevisionInfo`); what it built from and its report are the store's, read when asked for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from hitlcad.core.report import RevisionInfo
from hitlcad.core.types import DesignBrief, Pin, SelectionItem, WatchedMotion, utcnow
from hitlcad.kernel.errors import Diagnostic
from hitlcad.kernel.model import Model


class _Event(Model):
    seq: int = 0
    ts: datetime = Field(default_factory=utcnow)


class DesignOpened(_Event):
    type: Literal["design_opened"] = "design_opened"
    path: str
    name: str
    backend: str


class DesignUpdated(_Event):
    """design.json changed in a way the panel shows (name, backend, brief)."""

    type: Literal["design_updated"] = "design_updated"
    name: str
    backend: str
    brief: DesignBrief | None = None


class BuildStarted(_Event):
    type: Literal["build_started"] = "build_started"
    origin: str
    configuration: str | None = None


class RevisionCreated(_Event):
    type: Literal["revision_created"] = "revision_created"
    revision: RevisionInfo


class ErrorRaised(_Event):
    """Something stopped the design's work that is not the design's to fix: a build the machine or a bug stopped,
    a build/ folder that could not be written, a history that could not be read."""

    type: Literal["error"] = "error"
    diagnostic: Diagnostic


class SelectionChanged(_Event):
    type: Literal["selection_changed"] = "selection_changed"
    items: list[SelectionItem]


class PinsChanged(_Event):
    type: Literal["pins_changed"] = "pins_changed"
    pins: list[Pin]


class MotionsWatched(_Event):
    """The loop motions the human has watched move, all of them."""

    type: Literal["motions_watched"] = "motions_watched"
    watched: list[WatchedMotion]


class FeedbackDelivered(_Event):
    """The selection and open pins went to an agent session with a prompt (its hitlcad hook)."""

    type: Literal["feedback_delivered"] = "feedback_delivered"
    agent: str = Field(description="claude or codex")
    session: str
    selection: list[str] = Field(description="Ids of the selected items")
    pins: dict[str, str] = Field(description="Open pin id -> note")


Event = Annotated[
    DesignOpened
    | DesignUpdated
    | BuildStarted
    | RevisionCreated
    | ErrorRaised
    | SelectionChanged
    | PinsChanged
    | MotionsWatched
    | FeedbackDelivered,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class DesignState(Model):
    """Read model derived from the event log. Never mutated except by :meth:`apply`."""

    id: str
    path: str = ""
    name: str = ""
    backend: str = ""
    brief: DesignBrief | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    building: bool = False
    error: Diagnostic | None = Field(default=None, description="The latest error; cleared by the next revision")
    revisions: list[RevisionInfo] = Field(default_factory=list)
    selection: list[SelectionItem] = Field(default_factory=list)
    pins: list[Pin] = Field(default_factory=list)
    watched: list[WatchedMotion] = Field(default_factory=list)
    deliveries: dict[str, FeedbackDelivered] = Field(
        default_factory=dict, description="What each agent session was last given, by session id"
    )

    @property
    def current_revision(self) -> RevisionInfo | None:
        return self.revisions[-1] if self.revisions else None

    @property
    def open_pins(self) -> list[Pin]:
        return [p for p in self.pins if not p.resolved]

    def pointing(self) -> tuple[list[str], dict[str, str]]:
        """What the human points at, as a delivery records it: selected ids and open pin notes."""
        return [i.id for i in self.selection], {p.id: p.note for p in self.open_pins}

    def revision(self, rev_id: int) -> RevisionInfo | None:
        return next((r for r in self.revisions if r.id == rev_id), None)

    def before(self, rev_id: int, configuration: str | None) -> RevisionInfo | None:
        """The revision of a configuration built just before `rev_id`, whatever its status."""
        return next((r for r in reversed(self.revisions) if r.id < rev_id and r.configuration == configuration),
                    None)  # fmt: skip

    def latest(self, configuration: str | None) -> RevisionInfo | None:
        """The latest successful revision of one configuration (None: the default one)."""
        return next((r for r in reversed(self.revisions)
                     if r.status == "succeeded" and r.configuration == configuration), None)  # fmt: skip

    def apply(self, event: Event) -> None:
        self.updated_at = event.ts
        match event:
            case DesignOpened():
                self.path, self.name, self.backend = event.path, event.name, event.backend
                self.created_at = event.ts
            case DesignUpdated():
                self.name, self.backend, self.brief = event.name, event.backend, event.brief
            case BuildStarted():
                self.building = True
            case RevisionCreated():
                self.revisions.append(event.revision)
                self.building, self.error = False, None
            case ErrorRaised():
                self.building, self.error = False, event.diagnostic
            case SelectionChanged():
                self.selection = event.items
            case PinsChanged():
                self.pins = event.pins
            case MotionsWatched():
                self.watched = event.watched
            case FeedbackDelivered():
                self.deliveries[event.session] = event

    @classmethod
    def replay(cls, design_id: str, events: list[Event]) -> DesignState:
        state = cls(id=design_id)
        for event in events:
            state.apply(event)
        return state
