import assert from "node:assert/strict";
import * as path from "node:path";
import { test } from "node:test";
import { findDesignDir, isWithin } from "../lib/designLookup";
import { isHitlcadPyproject, resolveServerCommand } from "../lib/serverCommand";
import { panelHtml } from "../lib/panelHtml";
import { slugify } from "../lib/slug";
import { parseUiMessage, UiStates } from "../lib/uiState";


// ------------------------------------------------------------------ design lookup

function fakeFs(files: string[]): (p: string) => boolean {
  const set = new Set<string>();
  for (const f of files) {
    let p = path.resolve(f);
    set.add(p);
    while (path.dirname(p) !== p) {
      p = path.dirname(p);
      set.add(p);
    }
  }
  return (p) => set.has(path.resolve(p));
}

test("finds the nearest design.json above a file", () => {
  const exists = fakeFs(["/ws/designs/hook/design.json", "/ws/designs/hook/model.py", "/ws/designs/hook/lib/util.py"]);
  assert.equal(findDesignDir("/ws/designs/hook/model.py", "/ws", exists), "/ws/designs/hook");
  assert.equal(findDesignDir("/ws/designs/hook/lib/util.py", "/ws", exists), "/ws/designs/hook");
  assert.equal(findDesignDir("/ws/designs/hook", "/ws", exists), "/ws/designs/hook");
});

test("stops at the workspace root", () => {
  const exists = fakeFs(["/design.json", "/ws/notes/readme.md"]);
  assert.equal(findDesignDir("/ws/notes/readme.md", "/ws", exists), null);
});

test("walks to the filesystem root for files outside the workspace", () => {
  const exists = fakeFs(["/other/part/design.json", "/other/part/sub/model.py"]);
  assert.equal(findDesignDir("/other/part/sub/model.py", "/ws", exists), "/other/part");
  assert.equal(findDesignDir("/other/part/sub/model.py", null, exists), "/other/part");
});

test("isWithin handles siblings with a shared prefix", () => {
  assert.equal(isWithin("/ws/a", "/ws"), true);
  assert.equal(isWithin("/ws", "/ws"), true);
  assert.equal(isWithin("/ws2/a", "/ws"), false);
});

// ------------------------------------------------------------------ slug

test("slugify", () => {
  assert.equal(slugify("Wall Hook (v2)!"), "wall-hook-v2");
  assert.equal(slugify("  Ünïcode Bracket  "), "unicode-bracket");
  assert.equal(slugify("***"), "");
});

// ------------------------------------------------------------------ .mcp.json

test("pyproject detection", () => {
  assert.equal(isHitlcadPyproject('[project]\nname = "hitlcad"\nversion = "0.1"\n'), true);
  assert.equal(isHitlcadPyproject('[project]\nname = "other"\n[tool.x]\nname = "hitlcad"\n'), false);
  assert.equal(isHitlcadPyproject(null), false);
});

test("server command: installed tool first, then the dev checkout, else a clear error", () => {
  const dev = { path: "/src/hitlcad", pyproject: '[project]\nname = "hitlcad"\n' };
  const plain = { path: "/ws", pyproject: null };
  const has = (...exes: string[]) => (e: string) => exes.includes(e);
  assert.deepEqual(resolveServerCommand({ configured: ["hitlcad", "serve"], configuredCwd: "", folders: [plain], onPath: has("hitlcad") }), {
    ok: true, command: ["hitlcad", "serve"], cwd: undefined, source: "installed",
  });
  assert.deepEqual(resolveServerCommand({ configured: undefined, configuredCwd: "", folders: [plain, dev], onPath: has("uv") }), {
    ok: true, command: ["uv", "run", "hitlcad", "serve"], cwd: "/src/hitlcad", source: "dev",
  });
  const missing = resolveServerCommand({ configured: undefined, configuredCwd: "", folders: [plain], onPath: has("uv") });
  assert.equal(missing.ok, false);
  assert.match(missing.ok ? "" : missing.error, /uv tool install/);
  assert.deepEqual(resolveServerCommand({ configured: ["python", "-m", "hitlcad"], configuredCwd: "/x", folders: [], onPath: has() }), {
    ok: true, command: ["python", "-m", "hitlcad"], cwd: "/x", source: "configured",
  });
});

// ------------------------------------------------------------------ shared design UI state

test("parses only well-formed hitlcad page messages", () => {
  assert.deepEqual(parseUiMessage({ type: "hitlcad.ui.ready" }), { kind: "ready" });
  assert.deepEqual(parseUiMessage({ type: "hitlcad.ui", patch: { tab: "pins" }, reveal: true }), { kind: "patch", patch: { tab: "pins" }, reveal: true });
  assert.deepEqual(parseUiMessage({ type: "hitlcad.ui", patch: { compare: true } }), { kind: "patch", patch: { compare: true }, reveal: false });
  assert.deepEqual(parseUiMessage({ type: "hitlcad.selection", text: "In x I selected" }), { kind: "selection", text: "In x I selected" });
  assert.deepEqual(parseUiMessage({ type: "hitlcad.copy" }), { kind: "copy" });
  for (const bad of [null, "hitlcad.ui", [], { type: "hitlcad.ui", patch: [1] }, { type: "hitlcad.ui", patch: null }, { type: "hitlcad.selection", text: 3 }, { type: "other" }]) {
    assert.equal(parseUiMessage(bad), null);
  }
});

test("merges each design's UI patches separately", () => {
  const states = new UiStates();
  assert.deepEqual(states.get("dsg_a"), {});
  states.apply("dsg_a", { tab: "pins", compare: false });
  assert.deepEqual(states.apply("dsg_a", { compare: true }), { tab: "pins", compare: true });
  assert.deepEqual(states.apply("dsg_b", { revision: 3 }), { revision: 3 });
  assert.deepEqual(states.get("dsg_a"), { tab: "pins", compare: true });
});

// ------------------------------------------------------------------ panel page

test("the panel page loads the bundled UI and may reach only the server", () => {
  const html = panelHtml({
    assets: "https://file+.vscode-resource.vscode-cdn.net/ext/out/ui/",
    cspSource: "https://*.vscode-cdn.net",
    nonce: "n0nce",
    title: "hitlcad: <Hook>",
    config: { server: "http://127.0.0.1:8000/", view: { name: "file", path: '/tmp/a "b".step' } },
  });
  const csp = /Content-Security-Policy" content="([^"]*)"/.exec(html)![1]!;
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /script-src 'nonce-n0nce';/);
  assert.match(csp, /connect-src http:\/\/127\.0\.0\.1:8000 ws:\/\/127\.0\.0\.1:8000$/);
  assert.match(html, /<script type="module" nonce="n0nce" src="https:\/\/file\+\.vscode-resource\.vscode-cdn\.net\/ext\/out\/ui\/panel\.js">/);
  assert.match(html, /<title>hitlcad: &lt;Hook&gt;<\/title>/);
  const config = /data-config="([^"]*)"/.exec(html)![1]!.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
  assert.deepEqual(JSON.parse(config), { server: "http://127.0.0.1:8000/", view: { name: "file", path: '/tmp/a "b".step' } });
});
