"""Command line: ``hitlcad serve | doctor | build | init | connect | context``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from hitlcad.config import get_settings


def cmd_serve(_: argparse.Namespace) -> int:
    import uvicorn

    from hitlcad.api.rest import create_app
    from hitlcad.kernel.errors import HitlcadError, agent_text

    settings = get_settings()
    try:
        app = create_app()
    except HitlcadError as e:  # another server keeps the data folder, a backend that will not start
        print(agent_text(e.diagnostic), file=sys.stderr)
        return 1
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    from hitlcad.backends.registry import BackendRegistry
    from hitlcad.kernel.errors import agent_text
    from hitlcad.kernel.plugins import registry
    from hitlcad.render.base import NullRenderer, preview_renderer

    settings = get_settings()
    ok = True
    for b in BackendRegistry.from_settings(settings).describe():
        ok &= b.available
        print(f"[{'ok ' if b.available else 'MISSING'}] backend {b.name}: {b.detail}")
    renderer = not isinstance(preview_renderer(settings), NullRenderer)
    print(f"[{'ok ' if renderer else 'MISSING'}] preview renderer (Blender Workbench)")
    found = registry()
    for plugin in found.plugins.values():
        print(f"[ok ] plugin {plugin.name}: {plugin.distribution} {plugin.version}")
    for problem in found.failures:
        ok = False
        print(f"[FAIL] {agent_text(problem)}")
    print(f"[info] coding agents reach hitlcad's tools at {settings.mcp_url}")
    print(f"[info] data dir {settings.data_dir.resolve()}")
    print(f"[info] models build with {sys.executable}, the interpreter part files resolve their imports in")
    return 0 if ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    """Build a design folder through the full pipeline into a directory of its own, outside the server: for
    CI and for backend authors."""
    from hitlcad.agent.graph import revision_summary
    from hitlcad.app import build_pipeline
    from hitlcad.core.report import BuildReport, Revision
    from hitlcad.kernel.errors import HitlcadError, agent_text
    from hitlcad.render.base import NullRenderer
    from hitlcad.workspace.folder import DesignFolder

    settings = get_settings()
    pipeline = build_pipeline(settings, renderer=NullRenderer() if args.no_render else None)
    folder = DesignFolder(Path(args.design))
    try:
        spec = folder.spec(settings.tolerances, args.configuration)
    except HitlcadError as e:
        print(agent_text(e.diagnostic), file=sys.stderr)
        return 1
    out = Path(args.out).resolve()

    async def build() -> BuildReport:
        try:
            return await pipeline.run(spec, folder.path, out)
        finally:
            for backend in pipeline.registry.all():  # its workers end with it
                await backend.close()

    try:
        report = asyncio.run(build())
    except HitlcadError as e:
        print(agent_text(e.diagnostic), file=sys.stderr)
        return 1
    revision = Revision(id=0, origin="file", configuration=spec.configuration, spec=spec, report=report)
    print(revision_summary(revision, limit=settings.explore_limit))
    print(f"outputs in {out}")
    return 0 if report.status == "succeeded" and not report.failing() else 1


def _permission(flag: bool | None, question: str, option: str) -> bool:
    """The human's answer: from the command line flag, else asked on a terminal, else no."""
    if flag is not None:
        return flag
    if not sys.stdin.isatty():
        print(f"Skipped (not asked without a terminal; pass {option} to allow): {question.splitlines()[0]}")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def cmd_init(args: argparse.Namespace) -> int:
    from hitlcad.backends.registry import BackendRegistry
    from hitlcad.workspace import agents
    from hitlcad.workspace.project import init_project

    settings = get_settings()
    target = Path(args.path).expanduser().resolve()
    backends = BackendRegistry.from_settings(settings).all()
    hook = agents.command_line(agents.hook_argv(sys.executable, "<agent>", settings.base_url))
    hooks = _permission(args.hooks, (
        "Let Claude Code and Codex see what you select and pin in the hitlcad panel?\n"
        f"  This adds a prompt hook to {agents.CLAUDE_HOOKS} (yours, not committed) and to Codex's\n"
        f"  {agents.codex_hooks_file()}. Before each message you send, the agent runs:\n  {hook}\n"
        "  and your selection and open pins are added to the message."
    ), "--hooks")  # fmt: skip
    codex = _permission(args.codex, f"Add hitlcad's MCP server to Codex's {agents.codex_config_file()}?",
                        "--codex")  # fmt: skip
    written = init_project(target, settings, sys.executable, backends, hooks=hooks, codex=codex)
    print(f"design project ready at {target}")
    for item in written:
        print(f"  + {item}")
    print("Open it in VS Code; the hitlcad extension starts the server when needed.")
    if hooks:
        print("In Codex, approve hitlcad's hook once (/hooks); until then Codex skips it.")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """Print what the human selected or pinned in the panel. With --hook AGENT, act as that agent's
    UserPromptSubmit hook: read the hook's JSON from stdin and print what the session has not been
    told yet."""
    import urllib.parse
    import urllib.request

    from hitlcad.workspace.agents import SERVER_WAIT_S

    server = (args.server or get_settings().base_url).rstrip("/")

    def fetch(request: urllib.request.Request) -> str:
        with urllib.request.urlopen(request, timeout=SERVER_WAIT_S) as res:
            return res.read().decode()

    if args.hook:
        try:
            payload = json.load(sys.stdin)
            body = {"root": payload["cwd"], "agent": args.hook, "session": payload["session_id"]}
            text = fetch(urllib.request.Request(f"{server}/api/context/deliveries", data=json.dumps(body).encode(),
                                                headers={"Content-Type": "application/json"}, method="POST"))  # fmt: skip
        except Exception:  # a hook never fails the prompt: whatever went wrong, it adds nothing
            return 0
        if text:
            print(text)
        return 0
    query = urllib.parse.urlencode({"root": str(Path(args.root).resolve())})
    try:
        text = fetch(urllib.request.Request(f"{server}/api/context?{query}"))
    except OSError as e:
        print(f"hitlcad server not reachable at {server}: {e}", file=sys.stderr)
        return 1
    print(text or "Nothing is selected or pinned.")
    return 0


def cmd_connect(_: argparse.Namespace) -> int:
    import textwrap

    import tomlkit

    from hitlcad.workspace.agents import codex_server

    url = get_settings().mcp_url
    print("Claude Code:\n  claude mcp add --transport http hitlcad " + url)
    toml = tomlkit.dumps({"mcp_servers": {"hitlcad": codex_server(url)}})
    print("Codex (~/.codex/config.toml):\n" + textwrap.indent(toml, "  "), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hitlcad", description="Human-in-the-loop agentic CAD")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the MCP endpoint, the panel UI and its API (HITLCAD_HOST, HITLCAD_PORT)")
    p.set_defaults(fn=cmd_serve)

    sub.add_parser("doctor", help="check backends and renderer").set_defaults(fn=cmd_doctor)

    p = sub.add_parser("build", help="build and verify a design folder (exit 1 on a failed build or result)")
    p.add_argument("design", help="the design folder (holding design.json)")
    p.add_argument("--configuration", help="a named configuration")
    p.add_argument("--out", default="build-out")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("init", help="create a design project workspace (designs/, agent config)")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--hooks", action=argparse.BooleanOptionalAction,
                   help="add the agents' prompt hook (asked when not given)")  # fmt: skip
    p.add_argument("--codex", action=argparse.BooleanOptionalAction,
                   help="add hitlcad to Codex's config.toml and trust the project (asked when not given)")  # fmt: skip
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("context", help="print the panel's selections and open pins (the agents' prompt hook)")
    p.add_argument("--root", default=".", help="designs under this folder")
    p.add_argument("--hook", choices=["claude", "codex"], help="run as this agent's UserPromptSubmit hook")
    p.add_argument("--server", help="hitlcad server URL (default: from settings)")
    p.set_defaults(fn=cmd_context)

    p = sub.add_parser("connect", help="print commands to connect Claude Code / Codex over MCP")
    p.set_defaults(fn=cmd_connect)

    args = parser.parse_args(argv)
    return int(args.fn(args))


def run() -> None:
    """The ``hitlcad`` command."""
    # Reports print ✓ and ×, and the hook's JSON is UTF-8; a Windows console's own code page has neither.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main())


if __name__ == "__main__":
    run()
