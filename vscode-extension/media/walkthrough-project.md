# A design project

A design project is an ordinary folder for your CAD work. hitlcad adds:

- `designs/`, where each design gets its own folder
- `.mcp.json`, which connects Claude Code to the hitlcad server
- `CLAUDE.md` and `AGENTS.md`, which point Claude Code and Codex at hitlcad's skills
- `.claude/skills/` and `.agents/skills/`, hitlcad's skills: how to work with designs, and the code contract of each backend

It also asks whether your agents may see what you select and pin. If you allow it, it adds a prompt hook for Claude Code and Codex, and trusts the folder in Codex's config.

Existing files are never overwritten; only hitlcad's own `hitlcad*` skills are kept up to date.
