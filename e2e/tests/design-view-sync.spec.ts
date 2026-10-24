import Module from "node:module";
import * as path from "node:path";
import { type Browser, expect, type Page, test } from "@playwright/test";
import type { PanelView } from "../../vscode-extension/src/lib/panelHtml";
import { api, clickModel, fixtures, HARNESS_URL, panelUrl, viewerReady, watchCsp } from "../helpers";

/**
 * The 3D editor tab and the Design view are separate VS Code webviews kept in sync by the
 * extension. This runs the extension's real DesignUiHub; only VS Code's transport is simulated:
 * each webview is a page, and extension <-> webview messages go through Node.
 */

interface Webview {
  onDidReceiveMessage(cb: (m: unknown) => void): { dispose(): void };
  postMessage(m: unknown): Promise<unknown>;
}
interface Extension {
  DesignUiHub: new (reveal: () => void) => { attach(webview: Webview, designId: string): { dispose(): void } };
}

function loadExtension(): Extension {
  class Disposable {
    constructor(private readonly fn: () => void) {}
    dispose() {
      this.fn();
    }
  }
  const stub = "hitlcad-e2e-vscode-stub";
  const loader = Module as unknown as { _resolveFilename: (request: string, ...rest: unknown[]) => string };
  const resolve = loader._resolveFilename;
  loader._resolveFilename = function (request, ...rest) {
    return request === "vscode" ? stub : resolve.call(this, request, ...rest);
  };
  require.cache[stub] = { id: stub, filename: stub, loaded: true, exports: { Disposable } } as NodeJS.Module;
  return require(path.resolve(__dirname, "../../vscode-extension/out/designUi.js")) as Extension;
}

async function webview(browser: Browser, hub: InstanceType<Extension["DesignUiHub"]>, view: PanelView, height: number) {
  const page = await browser.newPage({ viewport: { width: 1000, height } });
  const listeners: ((m: unknown) => void)[] = [];
  const fake: Webview = {
    onDidReceiveMessage: (cb) => {
      listeners.push(cb);
      return { dispose: () => listeners.splice(listeners.indexOf(cb), 1) };
    },
    // extension -> webview; like VS Code's, it resolves false once the webview is gone instead of throwing
    postMessage: (m) => page.evaluate((msg) => window.postMessage(msg, "*"), m).then(() => true, () => false),
  };
  await watchCsp(page);
  await page.exposeFunction("__toExtension", (m: unknown) => listeners.forEach((cb) => cb(m))); // webview -> extension
  await page.addInitScript(() => {
    (window as unknown as { acquireVsCodeApi: unknown }).acquireVsCodeApi = () => ({
      postMessage: (m: unknown) => (window as unknown as { __toExtension: (m: unknown) => void }).__toExtension(m),
    });
  });
  const relay = hub.attach(fake, fixtures().designId);
  await page.goto(HARNESS_URL + panelUrl(view));
  const close = async () => {
    relay.dispose(); // first, so the hub stops posting to this page
    await page.close();
  };
  return { page, close };
}

const tab = async (inspector: Page) => (await inspector.locator(".tab.on").textContent())!.replace(/\d+$/, "");
const height = (page: Page) => page.getByLabel("Height", { exact: true }).first();

test("the editor tab and the Design view share revision, compare, tab and pin state", async ({ browser }) => {
  const { DesignUiHub } = loadExtension();
  let reveals = 0;
  const hub = new DesignUiHub(() => reveals++);
  const { designId } = fixtures();
  // Start without pins (other specs place some): the Pins tab offers "Place pins" only when none are open.
  for (const pin of (await api<{ pins: { id: string }[] }>("GET", `/api/designs/${designId}`)).pins) {
    await api("DELETE", `/api/designs/${designId}/pins/${pin.id}`);
  }
  const editor = await webview(browser, hub, { name: "design", id: designId }, 640);
  const inspector = await webview(browser, hub, { name: "inspector", id: designId }, 320);
  try {
  await expect(editor.page.locator(".cad-stage canvas")).toBeVisible();
  await expect(inspector.page.locator(".tabs")).toBeVisible();

  // Compare in the editor: the Design view switches to Changes and is revealed.
  await editor.page.locator(".compare-toggle").click();
  await expect.poll(() => tab(inspector.page)).toBe("Changes");
  expect(reveals).toBe(1);

  // Revision switch in the editor: the Design view's parameters follow it.
  await inspector.page.locator(".tab", { hasText: "Parameters" }).click();
  await expect(height(inspector.page)).toHaveValue("20");
  await editor.page.locator(".rev-switch button").first().click();
  await expect(height(inspector.page)).toHaveValue("10");
  await editor.page.locator(".rev-switch button").last().click();
  await expect(height(inspector.page)).toHaveValue("20");

  // "Place pins" in the Design view arms the editor; the placed pin comes back highlighted.
  await inspector.page.locator(".tab", { hasText: "Pins" }).click();
  await inspector.page.locator(".pins-panel .link-button", { hasText: "Place pins" }).click();
  await expect(editor.page.locator(".pin-toggle")).toHaveText("Placing pins");
  await viewerReady(editor.page);
  await clickModel(editor.page, 0.5, 0.42);
  await expect(inspector.page.locator(".pins li.active")).toHaveCount(1);
  expect(reveals).toBe(2);
  } finally {
    await editor.close();
    await inspector.close();
  }
});
