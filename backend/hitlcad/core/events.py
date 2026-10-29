"""Append-only design events and the projection that folds them into design state.

Each design has one event log: how its files changed, what was built, what the human selected and pinned, and
errors. The files themselves are the design's document (`workspace.document`), which the log records the versions
of; the log is the history around them. A revision's event lists it (`RevisionInfo`); what it built from and its
report are the store's, read when asked for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from hitlcad.core.report import Problem, Result, RevisionInfo, Tally, problems, tally
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


class BuildProgress(_Event):
    """The build running, as far as it has got: the revision it makes, its scene once its parts are placed in it
    (before anything is checked), what it does now, and what the analyses that have finished found. Live only, as
    `BuildStopped` is: published, never kept in the log, since neither says anything once the build ends; the
    revision or error it ends with is kept."""

    type: Literal["build_progress"] = "build_progress"
    revision: int
    configuration: str | None = None
    origin: str
    started: datetime = Field(description="When it started: one build's files of the revision from another's")
    scene: str | None = Field(default=None, description="The scene, in the revision's files, once the parts are in it")
    doing: str = ""
    results: list[Result] = Field(default_factory=list, description="What the analyses that have finished found, "
                                  "in the order they ran")  # fmt: skip
    problems: list[Problem] = Field(default_factory=list, description="Those that do not pass, by cause, as a "
                                    "report groups them")  # fmt: skip
    tally: Tally = Field(default_factory=Tally)

    @model_validator(mode="after")
    def _grouped(self) -> BuildProgress:
        self.problems = problems(self.results)
        self.tally = tally(self.results, self.problems)
        return self


class BuildStopped(_Event):
    """The build running stopped without a revision: newer files replaced it (live only)."""

    type: Literal["build_stopped"] = "build_stopped"


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
    """The traced motions the human has watched move, all of them."""

    type: Literal["motions_watched"] = "motions_watched"
    watched: list[WatchedMotion]


class DocumentChanged(_Event):
    """The design's files changed: a new version of its document, made by `origin` (the folder it was read from,
    a new design's files, the panel, an agent session, or a write to the folder by someone else)."""

    type: Literal["document_changed"] = "document_changed"
    version: int
    origin: str = Field(description="folder, scaffold, panel, agent:<agent>:<session> or disk")
    files: list[str] = Field(description="The files whose content changed")
    summary: str = Field(default="", description="What the edit was, where the editor says (the panel's)")


class CrossedEdit(Model):
    """Where two edits made on the same version changed the same text: the merge kept both, by rule."""

    path: str
    line: int = Field(description="The line in the version both started from; 0 for a file kept by its content")
    now: str = Field(description="The line as the merge left it")


class EditsCrossed(_Event):
    """An edit merged into others made since its version, where both changed the same text."""

    type: Literal["edits_crossed"] = "edits_crossed"
    version: int = Field(description="The version the merge made")
    origin: str = Field(description="Whose edit was merged in")
    others: list[str] = Field(description="Whose edits it crossed")
    crossed: list[CrossedEdit]


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
    | BuildProgress
    | BuildStopped
    | RevisionCreated
    | ErrorRaised
    | SelectionChanged
    | PinsChanged
    | MotionsWatched
    | DocumentChanged
    | EditsCrossed
    | FeedbackDelivered,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


#: How many of its latest changes and crossed edits a design's state holds.
KEPT = 20


class DesignState(Model):
    """Read model derived from the event log. Never mutated except by :meth:`apply`."""

    id: str
    path: str = ""
    name: str = ""
    backend: str = ""
    brief: DesignBrief | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    building: BuildProgress | None = Field(default=None, description="The build running, as far as it has got")
    error: Diagnostic | None = Field(default=None, description="The latest error; cleared by the next revision")
    revisions: list[RevisionInfo] = Field(default_factory=list)
    selection: list[SelectionItem] = Field(default_factory=list)
    pins: list[Pin] = Field(default_factory=list)
    watched: list[WatchedMotion] = Field(default_factory=list)
    deliveries: dict[str, FeedbackDelivered] = Field(
        default_factory=dict, description="What each agent session was last given, by session id"
    )
    version: int = Field(default=0, description="The latest version of the design's document")
    changes: list[DocumentChanged] = Field(default_factory=list, description=f"The latest {KEPT} changes of its files")
    crossed: list[EditsCrossed] = Field(
        default_factory=list, description=f"The latest {KEPT} edits that crossed others"
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
            case BuildProgress():
                self.building = event
            case BuildStopped():
                self.building = None
            case RevisionCreated():
                self.revisions.append(event.revision)
                self.building, self.error = None, None
            case ErrorRaised():
                self.building, self.error = None, event.diagnostic
            case SelectionChanged():
                self.selection = event.items
            case PinsChanged():
                self.pins = event.pins
            case MotionsWatched():
                self.watched = event.watched
            case DocumentChanged():
                self.version = event.version
                self.changes = [*self.changes, event][-KEPT:]
            case EditsCrossed():
                self.crossed = [*self.crossed, event][-KEPT:]
            case FeedbackDelivered():
                self.deliveries[event.session] = event

    @classmethod
    def replay(cls, design_id: str, events: list[Event]) -> DesignState:
        state = cls(id=design_id)
        for event in events:
            state.apply(event)
        return state
