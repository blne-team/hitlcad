/** What a panel webview shows: a design's 3D editor, its Design view, or a model file. */
export type PanelView = { name: "design"; id: string } | { name: "inspector"; id: string } | { name: "file"; path: string };

/** Handed to the panel UI (ui/src/lib/host.ts) in its page; `server` is the hitlcad server as the webview reaches it. */
export interface PanelConfig {
  server: string;
  view: PanelView;
}

export const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/**
 * The webview page that runs the panel UI. `assets` is the URL the webview serves out/ui/ under and
 * `cspSource` its resource origin. The page may load only those assets and reach only the server.
 */
export function panelHtml(opts: { assets: string; cspSource: string; nonce: string; title: string; config: PanelConfig }): string {
  const server = new URL(opts.config.server).origin;
  const csp = [
    "default-src 'none'",
    `script-src 'nonce-${opts.nonce}'`,
    `style-src ${opts.cspSource} 'unsafe-inline'`,
    `font-src ${opts.cspSource}`,
    `img-src ${opts.cspSource} ${server} data: blob:`,
    `connect-src ${server} ${server.replace(/^http/, "ws")}`,
  ].join("; ");
  const assets = opts.assets.replace(/\/+$/, "");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${esc(csp)}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${esc(opts.title)}</title>
<link rel="stylesheet" href="${esc(assets)}/panel.css">
</head>
<body>
<div id="root" data-config="${esc(JSON.stringify(opts.config))}"></div>
<script type="module" nonce="${opts.nonce}" src="${esc(assets)}/panel.js"></script>
</body>
</html>`;
}
