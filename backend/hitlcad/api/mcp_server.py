"""MCP tools that plug hitlcad into the user's own coding agent (Claude Code, Codex, …).

The agent keeps its normal workflow: it edits design files with its own tools and talks to the human in
its own chat. hitlcad adds what a coding agent cannot do alone: build and verify CAD assemblies, explore
their structure, find standard parts, measure geometry, and read what the human pointed at. Every tool
takes the design folder's absolute path; every name a tool answers with can be passed back to a tool.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from hitlcad import catalogs, describe, materials
from hitlcad.analysis import geometry
from hitlcad.app import HitlCad
from hitlcad.config import DEFAULT_BACKEND
from hitlcad.core.report import Revision
from hitlcad.graph import MARK, revision_summary
from hitlcad.render.section import joint_frames, section_png

INSTRUCTIONS = f"""\
hitlcad builds, verifies and renders parametric CAD assemblies. A design is a folder: design.json \
declares its parts (made or catalog), their connectors, the mates, relations and fastener sets \
between them, parameters, configurations and requirements; interfaces.py computes mating values \
once; parts/*.py build parts in their own frames; assembly.py places instances. The human reviews \
every build in the hitlcad panel in VS Code and talks to you in your own chat.

Work like this (the hitlcad skill has the details):
- create_design(path, name, backend) scaffolds a design. Declare the structure in design.json first, \
then build_design(path, scope="structure") checks it before any geometry exists.
- search_catalog(query) before modelling a screw, nut, insert or bearing.
- Write interfaces.py, the part files and assembly.py with your own file tools; saving rebuilds.
- build_design(path) verifies every connector, mate, relation, fastener set and requirement \
({describe.CHECK_KINDS}); it answers one line per problem (the checks one mistake fails, together), \
with section views through failing joints. \
scope="<part or instance>" answers about one part of a large product.
- explore_design(path, focus) answers about the design or one part, instance, connector, mate, \
parameter (with its impact), interfaces.<name>, configuration or 'rev N'. It also tells you what the \
human selected and pinned.
- list_geometry and measure_geometry measure exactly; compare_revisions shows what changed.
- Resolve pins you addressed with resolve_pins.

All tools take the design folder's absolute path."""


def build_mcp(core: HitlCad) -> MCPServer:
    designs, builds, feedback, explore, registry = core.designs, core.builds, core.feedback, core.explore, core.registry
    mcp = MCPServer("hitlcad", instructions=INSTRUCTIONS)

    def guard(fn: Any) -> Any:
        """Turn domain errors (no such design, part or revision; invalid files or arguments) into tool errors
        the agent can act on."""

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except (LookupError, ValueError) as e:
                raise ToolError(str(e)) from e

        return wrapper

    def images(design_id: str, rev: Revision) -> list[str | Image]:
        out: list[str | Image] = []
        preview = rev.report.artifact("preview")
        directory = feedback.revision_dir(design_id, rev)
        if preview and (directory / preview.path).is_file():
            out += [describe.PREVIEW_CAPTION, Image(data=(directory / preview.path).read_bytes(), format="png")]
        leads = [rev.report.results[p.findings[0].results[0]] for p in rev.report.problems if p.status == "fail"]
        failing = [r for r in leads if r.analysis in ("mate", "fastener", "relation")]  # one per problem
        if failing and rev.report.status == "succeeded":
            built = feedback.built(design_id, rev)
            for result in failing[:3]:
                cut = joint_frames(built, result)
                if cut is None:
                    continue
                frame, instances = cut
                png = section_png(
                    built, frame, instances, core.settings.section_size, f"{MARK['fail']} {result.subject}"
                )
                if png:
                    out += [
                        f"Section through {result.subject} (the plane holding its axis):",
                        Image(data=png, format="png"),
                    ]
        return out

    @mcp.tool(structured_output=False)
    @guard
    async def list_backends() -> str:
        """CAD backends with what each is good at, and the materials, catalog providers and exporters there are."""
        lines = ["Backends:"] + [f"- {b.name}: {b.title} — {b.summary}" for b in registry.available()]
        lines.append("Materials (add your own in a materials.json): " + materials.names(materials.builtin()))
        lines.append("Catalog providers: " + "; ".join(f"{p.name} ({p.usage})" for p in catalogs.providers().values()))
        lines.append("Exporters (design.json outputs): " + "; ".join(f"{e.name}: {e.summary}"
                                                                     for e in core.pipeline.exporters.values()))  # fmt: skip
        return "\n".join(lines)

    @mcp.tool(structured_output=False)
    @guard
    async def create_design(path: str, name: str, backend: str = DEFAULT_BACKEND) -> str:
        """Scaffold a design folder at `path` (absolute): design.json, interfaces.py, assembly.py and parts/.
        Declare the structure in design.json, then build_design(path, scope="structure")."""
        state, created = designs.open(path, backend, name)
        return (f"Created {', '.join(created) or 'nothing (the design already existed)'} in {state.path}. Read the "
                f"hitlcad and hitlcad-{state.backend} skills, declare parts, connectors and mates in design.json, then "
                "check them with build_design(path, scope='structure').")  # fmt: skip

    @mcp.tool(structured_output=False)
    @guard
    async def search_catalog(query: str, limit: int = 10) -> str:
        """Standard parts matching `query` ("M3 socket head screw", "608 bearing", "M3 heat-set insert"): the
        designation, the call that makes it in a part file, its sizes and its connectors."""
        items = catalogs.search(query, limit)
        if not items:
            return (
                f"Nothing matches {query!r}. Vendor parts (motors, boards) come from STEP files: hitlcad.catalogs.step."
            )
        lines = []
        for item in items:
            sizes = (
                f" · sizes {', '.join(item.sizes[:12])}{' …' if len(item.sizes) > 12 else ''}"
                if len(item.sizes) > 1
                else ""
            )
            lines.append(f"- {item.designation} [{item.family}, {item.provider}, {item.material}]: {item.call}{sizes}")
            lines.append("  connectors: " + "; ".join(f"{k} ({v})" for k, v in item.connectors.items()))
        return "\n".join(lines)

    @mcp.tool(structured_output=False)
    @guard
    async def build_design(path: str, scope: str | None = None, configuration: str | None = None,
                           summary: str = "") -> list[str | Image]:  # fmt: skip
        """Build and verify the design. scope="structure" checks design.json, interfaces.py and assembly.py
        without building geometry (seconds). scope="<part or instance>" builds (unchanged parts come from the
        cache) and answers about that part. configuration="<name>" builds one configuration, "all" every one.
        One line per problem, the checks one mistake fails named together; section views through failing joints."""
        state = designs.ensure(path)
        if scope == "structure":
            results, plan = await builds.check_structure(state.id)
            lines = [f"Structure of {state.name}:"]
            if plan and not plan.error:
                lines.append(f"  interface values: {len(plan.interfaces)}; placed: "
                             + ", ".join(f"{n} ×{len(p)}" if len(p) > 1 else n for n, p in plan.placements.items()))  # fmt: skip
            bad = [r for r in results if r.status != "pass"]
            lines += [f"  {MARK[r.status]} {r.subject}: {r.detail}" for r in bad] or ["  ✓ every reference resolves"]
            return ["\n".join(lines)]
        if configuration == "all":
            revisions = await builds.build_all(state.id, summary)
            out: list[str | Image] = [revision_summary(r, limit=core.settings.explore_limit) for r in revisions]
            return out + images(state.id, revisions[0])
        rev = await builds.build(state.id, summary, configuration=configuration)
        text = revision_summary(rev, [scope] if scope else None, limit=core.settings.explore_limit)
        if rev.report.status == "succeeded" and configuration is None:
            text += f"\nOutputs in {designs.folder(state.id).build_dir}/ (the human can open them there)."
        if pins := len(designs.get(state.id).open_pins):
            text += f"\n{pins} open pin(s) from the human: explore_design(path) lists them."
        return [text, *images(state.id, rev)]

    @mcp.tool(structured_output=False)
    @guard
    async def explore_design(path: str, focus: str | None = None, configuration: str | None = None) -> str:
        """The design graph. Without focus: the overview (failures, open pins, selection, parts, staleness).
        focus = a part, instance (planet[2], tilt/planet[2]), component, connector (lid.screws), mate (a+b),
        relation (a~b), fastener set, parameter (with what it affects), interfaces.<name>, configuration or 'rev N'."""
        state = designs.ensure(path)
        return await asyncio.to_thread(lambda: explore.graph(state.id, configuration).explore(focus))

    @mcp.tool(structured_output=False)
    @guard
    async def compare_revisions(path: str, base: int, target: int | None = None) -> str:
        """What changed from revision `base` to `target` (default: the latest): parameters, file diffs, results
        that changed status, parts rebuilt, size and mass."""
        return explore.compare(designs.ensure(path).id, base, target)

    @mcp.tool(structured_output=False)
    @guard
    async def list_geometry(path: str, target: str, kind: str = "faces", revision: int | None = None) -> str:
        """Every face (kind="faces"), edge or vertex of an instance ("lid[0]", placed) or a part ("part lid", its
        own frame) in the latest build (or `revision`), numbered as picks and measure_geometry number them:
        "face 9 of lid[0]" is faces()[9] of build/parts/lid.step, placed."""
        state = designs.ensure(path)
        rev = feedback.built_revision(state.id, revision)
        built = await asyncio.to_thread(feedback.built, state.id, rev)
        return f"Revision {rev.id}\n" + await asyncio.to_thread(geometry.list_geometry, built, target, kind)

    @mcp.tool(structured_output=False)
    @guard
    async def measure_geometry(path: str, a: str, b: str, revision: int | None = None) -> str:
        """Minimum distance between two addresses, with the angle where both have a direction and the gap between
        parallel faces: "face 9 of lid[0]", an instance "lid[0]", a part "part lid", a connector frame
        "lid.screws[2]" (its origin, Z as direction), or a point "(0, 0, 10)" in mm."""
        state = designs.ensure(path)
        rev = feedback.built_revision(state.id, revision)
        built = await asyncio.to_thread(feedback.built, state.id, rev)
        return f"Revision {rev.id}\n" + await asyncio.to_thread(geometry.measure, built, a, b)

    @mcp.tool(structured_output=False)
    @guard
    async def resolve_pins(path: str, pin_ids: list[str], reply: str = "") -> str:
        """Mark pins as addressed once your change is built; `reply` tells the human what you did."""
        state = designs.ensure(path)
        resolved = feedback.resolve_pins(state.id, pin_ids, reply)
        left = len(designs.get(state.id).open_pins)
        return f"Resolved {len(resolved)} pin(s); {left} still open."

    return mcp
