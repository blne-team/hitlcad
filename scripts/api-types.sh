#!/usr/bin/env bash
# Generate the API types of the extension and its panel UI (vscode-extension/src/lib/schema.ts) from the server's
# OpenAPI schema, which FastAPI derives from the pydantic models in backend/hitlcad. Run it after changing them;
# CI fails when schema.ts is out of date.
#
# Usage: scripts/api-types.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

HITLCAD_DATA_DIR="$work/data" uv run --project "$root" python -c \
  'import json; from hitlcad.api.app import create_app; print(json.dumps(create_app().openapi()))' >"$work/openapi.json"
npx --prefix "$root/vscode-extension" openapi-typescript "$work/openapi.json" -o "$root/vscode-extension/src/lib/schema.ts"
