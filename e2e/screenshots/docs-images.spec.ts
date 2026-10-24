import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { type Browser, expect, type Locator, type Page, test } from "@playwright/test";
import { api, openPanel, viewerReady, WORK } from "../helpers";

const EXAMPLES = path.resolve(__dirname, "../../examples");
const IMAGES = path.resolve(__dirname, "../../docs/images");
/** The 3D editor tab. */
const EDITOR = { width: 1280, height: 800 };
/** A gallery tile: three side by side in the README. */
const TILE = { width: 800, height: 800 };

/** Build a copy of examples/<name> (its build/ stays out of the repository) and return its design id. */
async function build(name: string): Promise<string> {
  const folder = path.join(WORK, "examples", name);
  fs.cpSync(path.join(EXAMPLES, name), folder, { recursive: true });
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder });
  await api("POST", `/api/designs/${id}/build`);
  return id;
}

const shot = (target: Page | Locator, file: string) => target.screenshot({ path: path.join(IMAGES, file) });

async function editor(page: Page, id: string, size = EDITOR) {
  await page.setViewportSize(size);
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
}

/** A tab of the Design view, at the size of a roomy bottom panel. */
async function designView(page: Page, id: string, tab: string, size: { width: number; height: number }) {
  await page.setViewportSize(size);
  await openPanel(page, { name: "inspector", id });
  await page.locator(".tab", { hasText: tab }).click();
  await expect(page.locator(".tab.on")).toContainText(tab);
  await page.waitForTimeout(500);
}

/**
 * Record what `act` does in a page of its own as docs/images/<file> (a GIF), with a cursor drawn in. The GIF
 * starts where `act` calls `start`.
 */
async function recording(browser: Browser, file: string, act: (page: Page, start: () => void) => Promise<void>) {
  const dir = path.join(WORK, "video");
  const context = await browser.newContext({ viewport: EDITOR, recordVideo: { dir, size: EDITOR } });
  const page = await context.newPage();
  const opened = Date.now();
  let from = 0;
  await page.addInitScript(() => {
    addEventListener("DOMContentLoaded", () => {
      const dot = document.createElement("div");
      dot.style.cssText = "position:fixed;z-index:99999;width:18px;height:18px;margin:-9px;border-radius:50%;background:rgba(20,20,20,.45);border:2px solid #fff;pointer-events:none;transition:transform .1s";
      document.body.append(dot);
      addEventListener("mousemove", (e) => (dot.style.left = `${e.clientX}px`, dot.style.top = `${e.clientY}px`), true);
      addEventListener("mousedown", () => (dot.style.transform = "scale(.7)"), true);
      addEventListener("mouseup", () => (dot.style.transform = ""), true);
    });
  });
  await act(page, () => (from = (Date.now() - opened) / 1000));
  await context.close();
  const video = await page.video()!.path();
  // Playwright's own ffmpeg records only; imageio-ffmpeg brings one that writes GIFs.
  const gif = `import subprocess, sys, imageio_ffmpeg
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", sys.argv[3], "-i", sys.argv[1], "-vf",
    "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer",
    sys.argv[2]], check=True)`;
  execFileSync("uv", ["run", "--no-project", "--with", "imageio-ffmpeg", "python", "-c", gif, video, path.join(IMAGES, file), from.toFixed(2)]);
}

/** The model alone, for the gallery: no grid, toolbar, tools or hints. */
async function model(page: Page, id: string, file: string) {
  await editor(page, id, TILE);
  await page.locator(".tcv_button_grid").click();
  await page.addStyleTag({
    content: ".tcv_cad_toolbar, .tcv_cad_navigation, .tcv_cad_info_wrapper, .tcv_toggle_tools_wrapper, .tcv_shape_filter, .viewport-hint, .motion-control { visibility: hidden !important; }",
  });
  await page.waitForTimeout(300);
  await shot(page.locator(".cad-stage canvas").first(), file);
}

test("handhold-fan", async ({ page, browser }) => {
  const id = await build("handhold-fan");
  await model(page, id, "gallery-fan.png");

  await designView(page, id, "Structure", { width: 1280, height: 600 });
  await shot(page, "design-structure.png");
  await designView(page, id, "Results", { width: 1000, height: 720 });
  await page.locator(".result-group summary", { hasText: "Requirements" }).click();
  await shot(page, "design-results.png");

  await recording(browser, "point-and-pin.gif", async (page, start) => {
    await openPanel(page, { name: "design", id });
    await viewerReady(page);
    await page.mouse.move(900, 600);
    start();
    await page.waitForTimeout(600);
    await page.mouse.move(520, 230, { steps: 25 });
    await page.waitForTimeout(400);
    await page.mouse.click(520, 230);
    await expect(page.locator(".selection-box li")).toHaveCount(1);
    await page.waitForTimeout(2200);
    const button = (await page.locator(".pin-toggle").boundingBox())!;
    const [bx, by] = [button.x + button.width / 2, button.y + button.height / 2];
    await page.mouse.move(bx, by, { steps: 20 });
    await page.mouse.click(bx, by);
    await expect(page.locator(".pin-toggle")).toHaveText("Placing pins");
    await page.mouse.move(700, 330, { steps: 25 });
    await page.waitForTimeout(400);
    await page.mouse.click(700, 330);
    await expect.poll(async () => (await api<{ pins: unknown[] }>("GET", `/api/designs/${id}`)).pins.length).toBe(1);
    await page.waitForTimeout(2200);
  });
});

test("happydog", async ({ page }) => {
  await model(page, await build("happydog"), "gallery-dog.png");
});

test("nas-case", async ({ page }) => {
  await model(page, await build("nas-case"), "gallery-nas.png");
});
