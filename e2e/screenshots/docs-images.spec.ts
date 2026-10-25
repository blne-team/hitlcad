import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { type Browser, expect, type Page, test } from "@playwright/test";
import { api, openPanel, viewerReady, WORK } from "../helpers";

const EXAMPLES = path.resolve(__dirname, "../../examples");
const IMAGES = path.resolve(__dirname, "../../docs/images");
/** The 3D editor tab. */
const EDITOR = { width: 1280, height: 800 };

/** Build a copy of examples/<name> (its build/ stays out of the repository) and return its design id. */
async function build(name: string): Promise<string> {
  const folder = path.join(WORK, "examples", name);
  fs.cpSync(path.join(EXAMPLES, name), folder, { recursive: true });
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder });
  await api("POST", `/api/designs/${id}/build`);
  return id;
}

const shot = (page: Page, file: string) => page.screenshot({ path: path.join(IMAGES, file) });

/** A tab of the Design view, at the size of a roomy bottom panel. */
async function designView(page: Page, id: string, tab: string, size: { width: number; height: number }) {
  await page.setViewportSize(size);
  await openPanel(page, { name: "inspector", id });
  await page.locator(".tab", { hasText: tab }).click();
  await expect(page.locator(".tab.on")).toContainText(tab);
  await page.waitForTimeout(500);
}

/** Everything over the model: the tours show the model alone. */
const BARE =
  ".review, .tcv_cad_toolbar, .tcv_cad_navigation, .tcv_cad_info_wrapper, .tcv_toggle_tools_wrapper, .tcv_shape_filter, .viewport-hint, .motion-control, .explode-spread { visibility: hidden !important; } .stagebar { display: none !important; }";

/** Write docs/images/<file>, a GIF `width` pixels wide at 12 frames a second, from ffmpeg's input arguments. */
function gif(input: string[], file: string, width: number) {
  // Playwright's own ffmpeg records only; imageio-ffmpeg brings one that writes GIFs.
  const script = `import subprocess, sys, imageio_ffmpeg
out, width, *inputs = sys.argv[1:]
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs, "-vf",
    f"fps=12,scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer",
    out], check=True)`;
  execFileSync("uv", ["run", "--no-project", "--with", "imageio-ffmpeg", "python", "-c", script, path.join(IMAGES, file), String(width), ...input]);
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
  gif(["-ss", from.toFixed(2), "-i", await page.video()!.path()], file, 960);
}

/** The editor a tour films: a smaller page draws faster in software GL. */
const STAGE = { width: 960, height: 600 };
const ease = (t: number) => 0.5 - Math.cos(Math.PI * t) / 2;

/**
 * A GIF made a frame at a time: each frame is taken once the view has drawn, however long software GL takes to
 * draw a large assembly, so the GIF plays at the pace the tour sets and not at the pace it rendered.
 */
class Film {
  private frames = 0;
  constructor(
    readonly page: Page,
    private readonly dir: string,
  ) {
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
  }

  private next = () => path.join(this.dir, `${String(this.frames++).padStart(4, "0")}.png`);

  async frame() {
    await this.page.evaluate(() => new Promise((drawn) => requestAnimationFrame(() => requestAnimationFrame(drawn))));
    await this.page.screenshot({ path: this.next() });
  }

  /** Nothing changes for `frames` frames. */
  hold(frames: number) {
    const last = path.join(this.dir, `${String(this.frames - 1).padStart(4, "0")}.png`);
    for (let i = 0; i < frames; i++) fs.copyFileSync(last, this.next());
  }

  /**
   * Move a slider from `from` to `to` (fractions of its range). It is set the way a drag sets it (the native
   * value setter, then an input event), so React's controlled sliders follow too.
   */
  async slide(slider: string, from: number, to: number, frames: number) {
    for (let i = 1; i <= frames; i++) {
      await this.page.locator(slider).evaluate((el: HTMLInputElement, f) => {
        const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
        set.call(el, String(Number(el.min) + f * (Number(el.max) - Number(el.min))));
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }, from + (to - from) * ease(i / frames));
      await this.frame();
    }
  }

  save(file: string) {
    gif(["-framerate", "12", "-i", path.join(this.dir, "%04d.png")], file, 720);
  }
}

/** What a tour shows after the model comes apart and goes back together. */
type Inside = (film: Film) => Promise<void>;

/** Hide `instances` with their show/hide toggles in the tree, then show them again. */
const peel =
  (instances: string[]): Inside =>
  async (film) => {
    const toggle = async () => {
      for (const instance of instances) {
        await film.page.locator(`.tv-tree-node[data-path$="/${instance}"] .tv-icon0`).first().dispatchEvent("click");
      }
      await film.frame();
    };
    await toggle();
    film.hold(16);
    await toggle();
  };

/** Move each of the first `count` mates of the motion control to `reach` of its range, and back. */
const swing =
  (count: number, reach = 0.25): Inside =>
  async (film) => {
    const slider = ".motion-control input[type=range]";
    for (let mate = 0; mate < count; mate++) {
      await film.page.locator(".motion-control select").evaluate((el: HTMLSelectElement, mate) => {
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(el, String(mate));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }, mate);
      await film.slide(slider, 0.5, 0.5 + reach, 8);
      film.hold(2);
      await film.slide(slider, 0.5 + reach, 0.5, 8);
    }
  };

/**
 * The model alone, in a few seconds: it comes apart into its parts (the explode's spread: each part moves
 * `explode` times its distance from the model's middle) and goes back together, then `inside` shows what the
 * outside hides. Filmed as docs/images/<file>.
 */
async function tour(browser: Browser, id: string, file: string, explode: number, inside: Inside) {
  const context = await browser.newContext({ viewport: STAGE });
  const film = new Film(await context.newPage(), path.join(WORK, "film"));
  await openPanel(film.page, { name: "design", id });
  await viewerReady(film.page);
  await film.page.locator(".tcv_button_grid").click();
  await film.page.addStyleTag({ content: BARE });
  await film.frame();
  film.hold(6);
  // The controls are hidden: driven by events. Explode on, at no spread yet; then the spread, frame by frame.
  const spread = ".explode-spread input"; // from 0 to 3: `slide` goes there by its fraction
  await film.page.locator(".tcv_button_explode").dispatchEvent("click");
  await film.slide(spread, 0, 0, 1);
  await film.page.waitForTimeout(500);
  await film.slide(spread, 0, explode / 3, 8);
  film.hold(10);
  await film.slide(spread, explode / 3, 0, 8);
  await film.page.locator(".tcv_button_explode").dispatchEvent("click");
  film.hold(4);
  await inside(film);
  film.hold(6);
  await context.close();
  film.save(file);
}

test("handhold-fan", async ({ page, browser }) => {
  const id = await build("handhold-fan");
  await tour(browser, id, "tour-fan.gif", 0.6, peel(["nozzle", "intake_guard", "duct"]));

  await designView(page, id, "Structure", { width: 1280, height: 600 });
  await shot(page, "design-structure.png");
  await designView(page, id, "Results", { width: 1000, height: 720 });
  await page.locator(".all-checks > summary").click();
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

test("robot-arm", async ({ page, browser }) => {
  const id = await build("robot-arm");
  await tour(browser, id, "tour-robot.gif", 0.25, swing(2));
});

test("happydog", async ({ page, browser }) => {
  const id = await build("happydog");
  await tour(browser, id, "tour-dog.gif", 0.6, async () => {});
});

test("nas-case", async ({ page, browser }) => {
  const id = await build("nas-case");
  await tour(browser, id, "tour-nas.gif", 0.4, peel(["side_left_rear", "side_right_rear", "rear_shell"]));
});
