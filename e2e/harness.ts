import { randomBytes } from "node:crypto";
import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";
import { panelHtml, type PanelView } from "../vscode-extension/src/lib/panelHtml";
import { BASE_URL, HARNESS_PORT, HARNESS_URL } from "./helpers";

const UI = path.resolve(__dirname, "../vscode-extension/out/ui");
const TYPES: Record<string, string> = { ".js": "text/javascript", ".css": "text/css", ".woff": "font/woff", ".woff2": "font/woff2" };

/** The page a webview gets for `view` (see panelUrl): the extension's own panelHtml, served from loopback. */
function page(view: PanelView): string {
  return panelHtml({ assets: `${HARNESS_URL}/ui`, cspSource: HARNESS_URL, nonce: randomBytes(16).toString("base64"), title: "webview", config: { server: BASE_URL, view } });
}

/**
 * Stands in for VS Code's webview resource server: /panel?view=… is a webview's page and /ui/ the built
 * panel UI (vscode-extension/out/ui). A real loopback server, as VS Code's webviews are local: Chrome's
 * Local Network Access checks block pages from elsewhere from calling 127.0.0.1.
 */
export async function startHarness(): Promise<() => Promise<void>> {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", HARNESS_URL);
    if (url.pathname === "/panel") {
      res.writeHead(200, { "Content-Type": "text/html" }).end(page(JSON.parse(url.searchParams.get("view")!) as PanelView));
      return;
    }
    const file = path.join(UI, path.normalize(url.pathname.replace(/^\/ui\//, "")));
    if (!url.pathname.startsWith("/ui/") || !file.startsWith(UI + path.sep) || !fs.existsSync(file)) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] ?? "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise<void>((done) => server.listen(HARNESS_PORT, "127.0.0.1", done));
  return () => new Promise((done) => server.close(() => done()));
}
