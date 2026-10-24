"""MCP tools that plug hitlcad into the user's own coding agent (Claude Code, Codex, …).

The agent keeps its normal workflow: it edits design files with its own tools and talks to the
human in its own chat. hitlcad adds what a coding agent cannot do alone: build CAD models,
measure and render them, and read what the human selected or pinned in the 3D panel.
Every tool takes the design folder's absolute path.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from mcp.server.mcpserver import Image, MCPServer

from hitlcad import describe
from hitlcad.analysis import geometry
from hitlcad.app import HitlCad
from hitlcad.core.types import Revision
from hitlcad.service.designs import NotFoundError

INSTRUCTIONS = f"""\
hitlcad builds, measures and renders parametric CAD designs. A design is a folder holding \
design.json (name, backend, parameters, optional brief) and model.py (defines build(p)). The \
human reviews every build in the hitlcad panel in VS Code: 3D view, checks, parameters, \
selections and pins. They talk to you in your own chat, not in hitlcad.

Work like this:
- create_design(path, name, backend) for a new design. The code contract for a backend is the \
hitlcad-<backend> skill that hitlcad init installs in the project: read it before writing model code.
- Edit model.py and design.json with your own file tools. Saving rebuilds the panel's view.
- build_design(path) returns measurements, checks, requirement results and renders: look at them \
before you report back. Put what the human asks for in design.json "requirements" with a check \
where one fits ({describe.CHECK_KINDS}), so every build verifies it.
- A design of several parts is an assembly: build(p) returns {{"base": base, "lid": lid}}; each part \
gets its own files, overlaps between parts are reported, and requirements can name a part.
- When the human says "this", "here", "the selected …" or mentions pins, call \
get_feedback(path). Resolve pins you addressed with resolve_pins.
- For build123d designs, list_geometry(path) shows every face (or edge) of the build with the \
numbers selections use, and measure_geometry(path, a, b) measures between them: check \
wall thicknesses, clearances and angles there rather than estimating from renders.

All tools take the design folder's absolute path."""


def _content(state_dir: Any, rev: Revision, extra: str = "") -> list[str | Image]:
    parts: list[str | Image] = [describe.revision_text(rev) + (f"\n\n{extra}" if extra else "")]
    png = describe.preview_png(rev, state_dir)
    if png:
        parts += [describe.PREVIEW_CAPTION, Image(data=png, format="png")]
    return parts


def build_mcp(core: HitlCad) -> MCPServer:
    designs, builds, feedback, registry = core.designs, core.builds, core.feedback, core.registry
    mcp = MCPServer("hitlcad", instructions=INSTRUCTIONS)

    def guard(fn: Any) -> Any:
        """Turn domain errors into tool results the agent can act on."""

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except (NotFoundError, ValueError, KeyError) as e:
                return f"ERROR: {e}"

        return wrapper

    @mcp.tool(structured_output=False)
    @guard
    async def list_backends() -> str:
        """List CAD backends with what each is good at."""
        lines = [f"- {b.name}: {b.title} — {b.summary}" for b in registry.available()]
        return "Available backends:\n" + "\n".join(lines)

    @mcp.tool(structured_output=False)
    @guard
    async def create_design(path: str, name: str, backend: str = "build123d") -> str:
        """Create a design folder at `path` (absolute) with its design.json, and show it in the panel. You write model.py."""
        state, created = designs.open(path, backend, name)
        return (f"Created {', '.join(created) or 'nothing (the design already existed)'} in {state.path}. "
                f"Read the hitlcad-{state.backend} skill, write model.py and the parameters in design.json; "
                f"saving model.py builds it, or call build_design.")  # fmt: skip

    @mcp.tool(structured_output=False)
    @guard
    async def get_design(path: str) -> str:
        """Current state of the design at `path`: latest build, brief, selection and open pins."""
        return describe.design_text(designs.ensure(path))

    @mcp.tool(structured_output=False)
    @guard
    async def build_design(path: str, summary: str = "") -> list[str | Image]:
        """Build the design's current files. Returns measurements, checks and renders of the new revision."""
        state = designs.ensure(path)
        rev = await builds.build(state.id, summary)
        notes = []
        if rev.report.status == "succeeded":
            notes.append(f"Outputs written to {designs.folder(state.id).build_dir}/ (the human can open them there).")
        if pins := len(designs.get(state.id).open_pins):
            notes.append(f"{pins} open pin(s) from the human: see get_feedback.")
        extra = "\n".join(notes)
        return _content(designs.store.revision_dir(state.id, rev.id), rev, extra)

    @mcp.tool(structured_output=False)
    @guard
    async def get_feedback(path: str) -> str:
        """What the human selected in the 3D view and the notes they pinned to the model (with pin ids)."""
        return describe.feedback_text(designs.ensure(path))

    @mcp.tool(structured_output=False)
    @guard
    async def resolve_pins(path: str, pin_ids: list[str], reply: str = "") -> str:
        """Mark pins as addressed once your change is built; `reply` tells the human what you did."""
        state = designs.ensure(path)
        resolved = feedback.resolve_pins(state.id, pin_ids, reply)
        left = len(designs.get(state.id).open_pins)
        return f"Resolved {len(resolved)} pin(s); {left} still open."

    @mcp.tool(structured_output=False)
    @guard
    async def get_revision(path: str, revision: int) -> str:
        """Source, parameters and report of an earlier revision, including ones built from the human's parameter edits."""
        rev = designs.ensure(path).revision(revision)
        if rev is None:
            raise NotFoundError(f"revision {revision} not found")
        return describe.revision_text(rev) + "\n\nSource:\n```python\n" + rev.spec.source + "\n```"

    @mcp.tool(structured_output=False)
    @guard
    async def list_geometry(
        path: str, kind: str = "faces", part: str | None = None, revision: int | None = None
    ) -> str:
        """Every face (kind="faces"), edge or vertex of a build123d design's latest build (or `revision`),
        numbered as the human's selections and pins name them: "face 9 of model" is faces()[9] of part
        `model` in import_step("build/model.step"). Each line gives the type, size and position."""
        rev, step = feedback.step_file(designs.ensure(path).id, revision)
        return f"Revision {rev.id}\n" + await asyncio.to_thread(geometry.list_geometry, step, kind, part)

    @mcp.tool(structured_output=False)
    @guard
    async def measure_geometry(path: str, a: str, b: str, revision: int | None = None) -> str:
        """Minimum distance between two faces, edges, vertices, parts or points of a build123d design's latest
        build (or `revision`), with the angle where both have a direction and the gap between parallel
        faces. Name them as selections do: "face 9", "edge 3 of block", a whole part "part lid", or a point
        "(0, 0, 10)" in mm."""
        rev, step = feedback.step_file(designs.ensure(path).id, revision)
        return f"Revision {rev.id}\n" + await asyncio.to_thread(geometry.measure, step, a, b)

    return mcp
