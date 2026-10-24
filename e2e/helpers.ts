import * as fs from "node:fs";
import * as path from "node:path";
import { expect, type Page } from "@playwright/test";
import type { PanelView } from "../vscode-extension/src/lib/panelHtml";

export const PORT = 8799;
export const BASE_URL = `http://127.0.0.1:${PORT}`;
/** Serves the panel UI as a webview would get it (harness.ts); the server lets this origin call it. */
export const HARNESS_PORT = 8798;
export const HARNESS_URL = `http://127.0.0.1:${HARNESS_PORT}`;
export const WORK = path.join(__dirname, ".work");
const FIXTURES = path.join(WORK, "fixtures.json");

/** What global-setup built: a build123d design with two revisions and the files of its first. */
export interface Fixtures {
  designId: string;
  step: string;
  stl: string;
  /** A two-part assembly: a base and a lid 0.5 mm above it. */
  assemblyId: string;
  assemblyFolder: string;
}

export const fixtures = (): Fixtures => JSON.parse(fs.readFileSync(FIXTURES, "utf8")) as Fixtures;
export const writeFixtures = (f: Fixtures) => fs.writeFileSync(FIXTURES, JSON.stringify(f, null, 2));

export async function api<T>(method: string, route: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE_URL + route, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${route}: ${res.status} ${text}`);
  return (text ? JSON.parse(text) : null) as T;
}

/** Write a design's files as an agent would: design.json (merged into what create_design scaffolded) and its code. */
export function writeDesign(folder: string, design: Record<string, unknown>, files: Record<string, string>) {
  const file = path.join(folder, "design.json");
  fs.writeFileSync(file, JSON.stringify({ ...JSON.parse(fs.readFileSync(file, "utf8")), ...design }, null, 2));
  for (const [rel, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(folder, rel)), { recursive: true });
    fs.writeFileSync(path.join(folder, rel), text);
  }
}

/** The harness page of a webview showing `view`. */
export const panelUrl = (view: PanelView) => `/panel?view=${encodeURIComponent(JSON.stringify(view))}`;

/** Record what the webview's Content-Security-Policy blocks, for viewerReady to report. */
export async function watchCsp(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { cspBlocked: string[] };
    w.cspBlocked = [];
    document.addEventListener("securitypolicyviolation", (e) => w.cspBlocked.push(`${e.effectiveDirective} ${e.blockedURI}`));
  });
}

/** Open `view` as a webview would show it, with a VS Code API that records what the page sends (see sentToExtension). */
export async function openPanel(page: Page, view: PanelView) {
  await watchCsp(page);
  await page.addInitScript(() => {
    const w = window as unknown as { acquireVsCodeApi?: () => unknown; sent: unknown[] };
    w.sent = [];
    w.acquireVsCodeApi ??= () => ({ postMessage: (m: unknown) => w.sent.push(m) });
  });
  await page.goto(panelUrl(view));
}

/** The types of the messages a page opened with openPanel sent to the extension, in order. */
export const sentToExtension = (page: Page) =>
  page.evaluate(() => (window as unknown as { sent: { type: string }[] }).sent.map((m) => m.type));

/** Wait until the 3D view has drawn the model, with nothing blocked by the webview's CSP. */
export async function viewerReady(page: Page) {
  await expect(page.locator(".cad-stage canvas")).toBeVisible();
  await page.waitForTimeout(800); // first frames and the GPU pick target
  await nothingBlocked(page);
}

/** Nothing the page loaded so far was blocked by the webview's CSP (see watchCsp). */
export async function nothingBlocked(page: Page) {
  expect(await page.evaluate(() => (window as unknown as { cspBlocked: string[] }).cspBlocked)).toEqual([]);
}

/** Click the model at a fraction of the canvas (hover first: the viewer resolves picks on hover). */
export async function clickModel(page: Page, fx: number, fy: number, opts: { shift?: boolean } = {}) {
  const box = await page.locator(".cad-stage canvas").boundingBox();
  if (!box) throw new Error("no canvas");
  const x = box.x + box.width * fx;
  const y = box.y + box.height * fy;
  await page.mouse.move(x - 4, y - 4);
  await page.mouse.move(x, y, { steps: 3 });
  if (opts.shift) await page.keyboard.down("Shift"); // mouse.click has no modifiers
  await page.mouse.click(x, y);
  if (opts.shift) await page.keyboard.up("Shift");
}
