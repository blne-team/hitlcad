"""The agents' hitlcad hook: Claude Code and Codex run it around each prompt and each of their tool calls that can
write a design's files (set up by `workspace.agents`). It hands the event to the server, which says what to add
(`services.sessions`):

- UserPromptSubmit: what the human points at in the panel, and what changed in the designs: the prompt's context;
- PreToolUse: nothing. The server leaves the designs the tool can write to it until it ends;
- PostToolUse, PostToolUseFailure: what others changed while the tool ran, and where the tool's edits crossed theirs:
  added to what the agent reads of the tool's result;
- Stop: nothing. The designs the turn wrote build now, once.

It finds the server where the server says it is (`SERVER_FILE`, in hitlcad's data folder), so the same hook serves
every project whatever port its server listens on. It runs twice around every tool call, so it starts on the fewest
modules of the standard library alone (``hitlcad/__init__.py`` is empty; urllib.request and argparse would add half
as long again), and whatever goes wrong, it adds nothing and exits 0: a hook never stops the agent (an agent takes
some exit codes as a refusal).

Usage: python -m hitlcad.hook {claude,codex}
"""

from __future__ import annotations

import http.client
import json
import os
import sys
from urllib.parse import urlsplit

SERVER_WAIT_S = 3  # how long the hook waits for the server before it adds nothing
#: In hitlcad's data folder while a server serves it: {"url": where the server listens}.
SERVER_FILE = "server.json"


def data_dir() -> str:
    """hitlcad's data folder, as its settings find it (`Settings.data_dir`)."""
    return os.environ.get("HITLCAD_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".hitlcad")


def output(event: str | None, text: str) -> str:
    """What to print for the agent: a prompt's context as text, a tool's as the JSON its hooks read."""
    if event in ("PostToolUse", "PostToolUseFailure"):
        return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})
    return text


def main(argv: list[str] | None = None) -> int:
    match argv if argv is not None else sys.argv[1:]:
        case ["claude" | "codex" as agent]:
            pass
        case _:
            print(__doc__.strip().splitlines()[-1], file=sys.stderr)
            return 0
    for stream in (sys.stdin, sys.stdout):  # the hook's JSON is UTF-8; a Windows console's own code page is not
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    try:
        event = json.load(sys.stdin)
        with open(os.path.join(data_dir(), SERVER_FILE), encoding="utf-8") as served:  # no server: nothing to add
            url = urlsplit(json.load(served)["url"])
        connection = http.client.HTTPConnection(url.hostname or "127.0.0.1", url.port or 80, timeout=SERVER_WAIT_S)
        connection.request("POST", f"{url.path.rstrip('/')}/api/agents/hook", json.dumps({"agent": agent, "event": event}),
                           {"Content-Type": "application/json"})  # fmt: skip
        res = connection.getresponse()
        text = res.read().decode() if res.status == 200 else ""
    except Exception:  # whatever went wrong, it adds nothing
        return 0
    if text:
        print(output(event.get("hook_event_name"), text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
