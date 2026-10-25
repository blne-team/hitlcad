"""Command line: ``hitlcad serve | doctor | build | init | connect | context``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from hitlcad.config import get_settings


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hitlcad.api.app import create_app

    settings = get_settings()
    uvicorn.run(create_app(), host=args.host or settings.host, port=args.port or settings.port, log_level="info")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    from hitlcad.backends.registry import BackendRegistry
    from hitlcad.render.base import BlenderWorkbenchRenderer

    settings = get_settings()
    ok = True
    for b in BackendRegistry.from_settings(settings).describe():
        ok &= b.available
        print(f"[{'ok ' if b.available else 'MISSING'}] backend {b.name}: {b.detail}")
    renderer = BlenderWorkbenchRenderer(binary=settings.blender_binary).available()
    print(f"[{'ok ' if renderer else 'MISSING'}] preview renderer (Blender Workbench)")
    print(f"[info] coding agents reach hitlcad's tools at {settings.mcp_url}")
    print(f"[info] data dir {settings.data_dir.resolve()}")
    return 0 if ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    """Build a design folder through the full pipeline into a directory of its own, outside the server: for
    CI and for backend authors."""
    from hitlcad.analysis.base import default_analyses
    from hitlcad.backends.registry import BackendRegistry
    from hitlcad.build.pipeline import BuildPipeline
    from hitlcad.core.report import Revision
    from hitlcad.designs.folder import DesignFolder
    from hitlcad.exporters import discover
    from hitlcad.graph import revision_summary
    from hitlcad.render.base import BlenderWorkbenchRenderer

    settings = get_settings()
    registry = BackendRegistry.from_settings(settings)
    renderer = None if args.no_render else BlenderWorkbenchRenderer(binary=settings.blender_binary)
    pipeline = BuildPipeline(registry, settings, default_analyses(), discover(), renderer)
    folder = DesignFolder(Path(args.design))
    try:
        spec = folder.spec(settings.tolerances, args.configuration)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = Path(args.out).resolve()
    report = asyncio.run(pipeline.run(spec, folder.path, out))
    print(revision_summary(Revision(id=0, origin="file", spec=spec, report=report), limit=settings.explore_limit))
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
    from hitlcad.designs import agents
    from hitlcad.designs.project import init_project

    settings = get_settings()
    target = Path(args.path).expanduser().resolve()
    backends = BackendRegistry.from_settings(settings).all()
    hook = agents.command_line(agents.hook_argv(sys.executable, "<agent>", settings.base_url))
    hooks = _permission(args.hooks, (
        "Let Claude Code and Codex see what you select and pin in the hitlcad panel?\n"
        f"  This adds a prompt hook to {agents.CLAUDE_SETTINGS} and {agents.CODEX_HOOKS}. Before each message\n"
        f"  you send, the agent runs: {hook}\n  and your selection and open pins are added to the message."
    ), "--hooks")  # fmt: skip
    codex = _permission(args.codex, (
        f"Set up Codex in {agents.codex_config_file()}?\n"
        f"  This adds hitlcad's MCP server and trusts {target}, so Codex loads the project's hook."
    ), "--codex")  # fmt: skip
    written = init_project(target, settings.base_url, sys.executable, backends, hooks=hooks, codex=codex)
    print(f"design project ready at {target}")
    for item in written:
        print(f"  + {item}")
    print("Open it in VS Code; the hitlcad extension starts the server when needed.")
    if hooks:
        print("In Codex, approve the project's hook once (/hooks); until then Codex skips it.")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """Print what the human selected or pinned in the panel. With --hook AGENT, act as that agent's
    UserPromptSubmit hook: read the hook's JSON from stdin and print what the session has not been
    told yet. A hook never fails the prompt."""
    import urllib.parse
    import urllib.request

    server = (args.server or get_settings().base_url).rstrip("/")
    try:
        if args.hook:
            payload = json.load(sys.stdin)
            body = {"root": payload["cwd"], "agent": args.hook, "session": payload["session_id"]}
            request = urllib.request.Request(f"{server}/api/context/deliveries", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")  # fmt: skip
        else:
            query = urllib.parse.urlencode({"root": str(Path(args.root).resolve())})
            request = urllib.request.Request(f"{server}/api/context?{query}")
        with urllib.request.urlopen(request, timeout=3) as res:
            text = res.read().decode()
    except Exception as e:
        if args.hook:
            return 0  # the server is down, or the payload is not what we expect: add nothing
        print(f"hitlcad server not reachable at {server}: {e}", file=sys.stderr)
        return 1
    if text:
        print(text)
    elif not args.hook:
        print("Nothing is selected or pinned.")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    settings = get_settings()
    url = f"http://{args.host or settings.host}:{args.port or settings.port}/mcp"
    print("Claude Code:\n  claude mcp add --transport http hitlcad " + url)
    print(
        'Codex (~/.codex/config.toml):\n  [mcp_servers.hitlcad]\n  url = "'
        + url
        + '"\n  default_tools_approval_mode = "approve"'
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hitlcad", description="Human-in-the-loop agentic CAD")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the MCP endpoint, the panel UI and its API")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
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
    p.add_argument("--host")
    p.add_argument("--port", type=int)
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
