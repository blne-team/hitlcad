import * as path from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { api, clickModel, fixtures, openPanel, viewerReady, WORK, writeDesign } from "../helpers";

let id: string;

test.beforeAll(async () => {
  const folder = path.join(WORK, "project", "designs", "grid-height");
  ({ id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Grid height", backend: "build123d" } }));
  writeDesign(folder, {
    parameters: [{ name: "elevation", value: 25 }], ground: "box",
    parts: { box: { role: "offset part", file: "parts/box.py", material: "PLA" } },
  }, {
    "parts/box.py": `from build123d import *
def build(p, i):
    return {"box": (Box(30, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)), {})}
`,
    "assembly.py": `from build123d import Pos
def build(p, i):
    return {"box": [Pos(20, -15, p["elevation"])]}
`,
  });
  await api("POST", `/api/designs/${id}/build`);
  await api("POST", `/api/designs/${id}/parameters`, { values: { elevation: -15 }, expected: { elevation: 25 } });
});

/** Observe real Three.js render state, so assertions distinguish camera pan from rotation and geometry moves.
 * The hook lives only in the test page; the application needs no test API. */
async function observeViewer(page: Page) {
  await page.evaluate(() => {
    const w = window as any;
    delete w.__viewCamera;
    delete w.__viewScene;
    const original = w.THREE.Object3D.prototype.updateMatrixWorld;
    w.THREE.Object3D.prototype.updateMatrixWorld = function (...args: any[]) {
      if (this.isScene && this.children.some((c: any) => "centerGrid" in c)) w.__viewScene = this;
      // The first camera renders the model; a separate camera later renders the orientation marker.
      if (this.isOrthographicCamera && !this.parent && !w.__viewCamera) w.__viewCamera = this;
      return original.apply(this, args);
    };
  });
  await page.locator(".tcv_button_iso").click();
  await expect.poll(() => viewState(page)).not.toBeNull();
}

async function viewState(page: Page) {
  return page.evaluate(() => {
    const w = window as any;
    const scene = w.__viewScene;
    const camera = w.__viewCamera;
    if (!scene || !camera) return null;
    const grid = scene.children.find((c: any) => "centerGrid" in c);
    const parts: { min: number[]; max: number[] }[] = [];
    scene.traverse((o: any) => {
      if (o.isObjectGroup && o.front) {
        const box = new w.THREE.Box3().setFromObject(o.front, true);
        parts.push({ min: box.min.toArray(), max: box.max.toArray() });
      }
    });
    return {
      position: camera.position.toArray() as number[], quaternion: camera.quaternion.toArray() as number[], zoom: camera.zoom as number,
      gridZ: grid.children[0].getWorldPosition(new w.THREE.Vector3()).z as number, parts,
    };
  });
}

test("Pan moves the view without rotating or selecting, and Inspect returns to selection", async ({ page }) => {
  await openPanel(page, { name: "file", path: fixtures().step });
  await viewerReady(page);
  await observeViewer(page);
  const before = (await viewState(page))!;
  const pan = page.getByRole("button", { name: "Pan view", exact: true });
  await pan.click();
  await expect(pan).toHaveAttribute("aria-pressed", "true");
  const canvas = page.locator(".cad-stage canvas");
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.42);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.48, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => (await viewState(page))!.position).not.toEqual(before.position);
  const after = (await viewState(page))!;
  after.quaternion.forEach((v, i) => expect(v).toBeCloseTo(before.quaternion[i], 8));
  expect(after.zoom).toBe(before.zoom);
  expect(after.parts).toEqual(before.parts);
  await expect(page.locator(".selection-box")).toBeHidden();
  await page.getByRole("button", { name: "Inspect model", exact: true }).click();
  await expect(pan).toHaveAttribute("aria-pressed", "false");
  await clickModel(page, 0.4, 0.48);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  // Native right-drag is an alternative to entering Pan mode.
  const prior = (await viewState(page))!;
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.down({ button: "right" });
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.5, { steps: 8 });
  await page.mouse.up({ button: "right" });
  await expect.poll(async () => (await viewState(page))!.position).not.toEqual(prior.position);
  (await viewState(page))!.quaternion.forEach((v, i) => expect(v).toBeCloseTo(prior.quaternion[i], 8));
});

test("Pan yields to measuring and pin placement", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const pan = page.getByRole("button", { name: "Pan view", exact: true });
  await page.locator(".tcv_button_distance").click();
  await pan.click();
  await expect(pan).toHaveAttribute("aria-pressed", "true");
  await pan.click();
  await clickModel(page, 0.5, 0.42);
  await expect(page.locator(".selection-box li")).toHaveCount(1); // entering Pan disabled measuring
  await page.locator(".selection-box").getByRole("button", { name: "Clear", exact: true }).click();
  await pan.click();
  await page.locator(".tcv_button_properties").click();
  await expect(pan).toHaveAttribute("aria-pressed", "false");
  await pan.click();
  await page.getByRole("button", { name: "Place pins", exact: true }).click();
  await expect(pan).toHaveAttribute("aria-pressed", "false");
});

test("the base grid follows positive and negative placements without moving the model", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await observeViewer(page);
  for (const z of [-15, 25]) {
    if (z === 25) {
      await page.getByRole("button", { name: "Previous revision", exact: true }).click();
      await observeViewer(page);
    }
    const placed = (await viewState(page))!;
    expect(placed.parts[0].min).toEqual([5, -25, z]);
    expect(placed.gridZ).toBe(z);
    await page.screenshot({ path: path.join(WORK, `grid-base-${z}.png`) });
    await page.locator(".tcv_button_grid").hover();
    const base = page.getByRole("checkbox", { name: "At model base", exact: true });
    await expect(base).toBeChecked();
    await base.uncheck();
    await expect.poll(async () => (await viewState(page))!.gridZ).toBe(0);
    expect((await viewState(page))!.parts).toEqual(placed.parts);
    await base.check();
    await expect.poll(async () => (await viewState(page))!.gridZ).toBe(z);
    await page.locator(".tcv_button_axes0").click();
    await expect.poll(async () => (await viewState(page))!.gridZ).toBe(z);
    await page.locator(".tcv_button_axes0").click();
    await page.evaluate(() => document.body.dataset.vscodeThemeKind = "vscode-dark");
    await page.setViewportSize({ width: 900, height: 650 });
    const box = (await page.locator(".cad-stage canvas").boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
    const zoom = (await viewState(page))!.zoom;
    await page.mouse.wheel(0, -300);
    await expect.poll(async () => (await viewState(page))!.zoom).not.toBe(zoom);
    await expect.poll(async () => (await viewState(page))!.gridZ).toBe(z);
    expect((await viewState(page))!.parts).toEqual(placed.parts);
  }
});

test("a rotated curved part rests on the grid rather than its oversized bounding box", async ({ page }) => {
  const folder = path.join(WORK, "project", "designs", "curved-grid");
  const { id: curvedId } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Curved grid", backend: "build123d" } });
  writeDesign(folder, { ground: "ball", parts: { ball: { role: "rotated sphere", file: "parts/ball.py", material: "PLA" } } }, {
    "parts/ball.py": `from build123d import Sphere
def build(p, i):
    return {"ball": (Sphere(5), {})}
`,
    "assembly.py": `from build123d import Pos, Rot
def build(p, i):
    return {"ball": [Pos(0, 0, 25) * Rot(0, 45, 0)]}
`,
  });
  const built = await api<{ id: number }>("POST", `/api/designs/${curvedId}/build`);
  const scene = await api<{ shapes: { bb: { zmin: number } } }>("GET", `/api/designs/${curvedId}/revisions/${built.id}/files/scene.json`);
  await openPanel(page, { name: "design", id: curvedId });
  await viewerReady(page);
  await observeViewer(page);
  const visible = (await viewState(page))!;
  expect(visible.gridZ).toBeCloseTo(20, 2);
  expect(visible.gridZ).toBeCloseTo(visible.parts[0].min[2], 8);
  expect(visible.gridZ - scene.shapes.bb.zmin).toBeGreaterThan(1); // bounding-box corners lie below the sphere
  await page.screenshot({ path: path.join(WORK, "grid-rotated-sphere.png") });
});
