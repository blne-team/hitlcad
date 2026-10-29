import * as fs from "node:fs";
import * as path from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { api, BASE_URL, clickModel, openPanel, sentToExtension, viewerReady, WORK, writeDesign } from "../helpers";

/** Every control of the 3D editor tab and the Design view does what it says, on a design of its own. */

const MODEL = `from build123d import *


def build(p, i):
    return {"box": (Box(p["width"], 20, p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN)), {})}
`;
const ASSEMBLY = `from build123d import Pos


def build(p, i):
    return {"box": [Pos()]}
`;

interface State {
  revisions: { id: number }[];
  selection: unknown[];
  pins: { id: string; note: string; resolved: boolean }[];
}

let id = "";
let folder = "";
const state = () => api<State>("GET", `/api/designs/${id}`);

test.beforeAll(async () => {
  folder = path.join(WORK, "project", "designs", "controls");
  ({ id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Controls", backend: "build123d" } }));
  const parameters = [
    { name: "width", label: "Width", value: 30, min: 1, max: 100 },
    { name: "height", label: "Height", value: 10, min: 1, max: 100 },
  ];
  writeDesign(folder, { parameters, ground: "box", parts: { box: { role: "a box", file: "parts/box.py", material: "PLA" } } },
    { "parts/box.py": MODEL, "assembly.py": ASSEMBLY });
  const built = await api<{ version: number }>("POST", `/api/designs/${id}/build`);
  await api("POST", `/api/designs/${id}/parameters`, { values: { height: 15 }, base: built.version });
});

const tab = (page: Page, name: string) => page.locator(".tab", { hasText: name }).click();

test("the Design view's tabs each show their panel", async ({ page }) => {
  await openPanel(page, { name: "inspector", id });
  for (const [name, shows] of [
    ["Parameters", page.getByRole("button", { name: "Rebuild with these values" })],
    ["Changes", page.locator(".changes-pick select")],
    ["Results", page.locator("h3", { hasText: "Files" })],
    ["Structure", page.locator(".structure svg")],
    ["BOM", page.locator(".bom-table")],
    ["Brief", page.locator(".tab-body")],
    ["Pins", page.locator(".pins-panel")],
  ] as const) {
    await tab(page, name);
    await expect(page.locator(".tab.on")).toContainText(name);
    await expect(shows).toBeVisible();
  }
});

test("pins: write a note, resolve, reopen, remove", async ({ page }) => {
  for (const pin of (await state()).pins) await api("DELETE", `/api/designs/${id}/pins/${pin.id}`);
  const onPart = { instance: "box", topo: "solid" };
  await api("POST", `/api/designs/${id}/pins`, { point: [0, 0, 15], component: onPart });
  await api("POST", `/api/designs/${id}/pins`, { point: [0, -10, 5], component: onPart });
  await openPanel(page, { name: "inspector", id });

  const note = page.getByLabel("Note for pin 1");
  await note.fill("round this edge");
  await note.press("Enter");
  await expect.poll(async () => (await state()).pins[0]?.note).toBe("round this edge");
  await page.locator(".pins-panel").getByRole("button", { name: "Copy for chat" }).click();
  expect(await sentToExtension(page)).toContain("hitlcad.copy");

  await page.locator(".pins li").first().getByRole("button", { name: "Resolve" }).click();
  await expect.poll(async () => (await state()).pins.filter((p) => p.resolved).length).toBe(1);
  await page.locator(".resolved-pins summary").click();
  await page.locator(".resolved-pins").getByRole("button", { name: "Reopen" }).click();
  await expect.poll(async () => (await state()).pins.filter((p) => p.resolved).length).toBe(0);

  await page.getByRole("button", { name: "Remove pin 2" }).click();
  await expect.poll(async () => (await state()).pins.length).toBe(1);
  await page.locator(".pins li").first().getByRole("button", { name: "Resolve" }).click();
  await page.locator(".resolved-pins summary").click();
  await page.locator(".resolved-pins").getByRole("button", { name: "Delete" }).click();
  await expect.poll(async () => (await state()).pins.length).toBe(0);
  await expect(page.locator(".pins-panel .link-button", { hasText: "Place pins" })).toBeVisible();
});

test("parameters: reset restores, rebuild writes design.json and builds", async ({ page }) => {
  await openPanel(page, { name: "inspector", id });
  await tab(page, "Parameters");
  const width = page.getByLabel("Width", { exact: true });
  const rebuild = page.getByRole("button", { name: "Rebuild with these values" });
  await expect(rebuild).toBeDisabled();

  await width.fill("42");
  await expect(rebuild).toBeEnabled();
  await page.getByRole("button", { name: "Reset" }).click();
  await expect(width).toHaveValue("30");
  await expect(rebuild).toBeDisabled();

  await width.fill(""); // an emptied field is no value: it stays empty and holds back rebuilding
  await expect(width).toHaveValue("");
  await expect(rebuild).toBeDisabled();
  await page.getByRole("button", { name: "Reset" }).click();
  await expect(width).toHaveValue("30");

  const before = (await state()).revisions.length;
  await page.getByLabel("Width slider").fill("35.3"); // the slider moves in round 0.1 mm steps
  await rebuild.click();
  await expect.poll(async () => (await state()).revisions.length, { timeout: 60_000 }).toBe(before + 1);
  const saved = JSON.parse(fs.readFileSync(path.join(folder, "design.json"), "utf8")) as { parameters: { name: string; value: number }[] };
  expect(saved.parameters.find((p) => p.name === "width")?.value).toBe(35.3);
  await expect(width).toHaveValue("35.3"); // the panel follows the new revision
});

test("changes: compare the revision on screen with any earlier one", async ({ page }) => {
  await openPanel(page, { name: "inspector", id });
  await tab(page, "Changes");
  const base = page.locator(".changes-pick select");
  const options = await base.locator("option").count();
  expect(options).toBeGreaterThan(1); // run after the rebuild above: three revisions
  await base.selectOption({ index: 0 }); // revision 1: both height and width differ
  await expect(page.locator(".changes .metrics").first()).toContainText("Height");
  await expect(page.locator(".changes .metrics").first()).toContainText("Width");
  await base.selectOption({ index: options - 1 }); // the one just before: only width changed
  await expect(page.locator(".changes .metrics").first()).not.toContainText("Height");
});

test("results: each listed file asks the extension to save it, and is served", async ({ page, request }) => {
  await openPanel(page, { name: "inspector", id });
  await tab(page, "Results");
  const files = page.locator(".files button");
  const count = await files.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i++) await files.nth(i).click();
  const asked = await page.evaluate(() =>
    (window as unknown as { sent: { type: string; design: string; revision: number; path: string }[] }).sent.filter((m) => m.type === "hitlcad.saveFile"),
  );
  expect(asked.map((m) => m.path)).toEqual(await files.allTextContents());
  for (const m of asked) {
    expect(m.design).toBe(id);
    const url = `${BASE_URL}/api/designs/${m.design}/revisions/${m.revision}/files/${m.path}`;
    expect((await request.get(url)).status(), url).toBe(200);
  }
});

test("the editor: revision arrows, selection copy and clear", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const [previous, next] = [page.locator(".rev-switch button").first(), page.locator(".rev-switch button").last()];
  const count = (await state()).revisions.length;
  await expect(page.locator(".rev-switch")).toContainText(`Revision ${count} of ${count}`);
  await expect(next).toBeDisabled();
  await previous.click();
  await expect(page.locator(".rev-switch")).toContainText(`Revision ${count - 1} of ${count}`);
  await next.click();
  await expect(next).toBeDisabled();

  await clickModel(page, 0.5, 0.42);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  await page.locator(".selection-box").getByRole("button", { name: "Copy for chat" }).click();
  expect(await sentToExtension(page)).toContain("hitlcad.copy");
  await page.locator(".selection-box").getByRole("button", { name: "Clear" }).click();
  await expect(page.locator(".selection-box")).toBeHidden();
  await expect.poll(async () => (await state()).selection.length).toBe(0);
});

test("the hint on how to select closes, and asks the extension to keep it closed", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const hint = page.locator(".select-hint");
  await expect(hint).toContainText("Shift-click adds more.");
  await hint.getByRole("button", { name: "Hide this hint" }).click();
  await expect(hint).toHaveCount(0);
  expect(await sentToExtension(page)).toContain("hitlcad.hideSelectionHint"); // hitlcad.selectionHint: false
});

test("the viewer's measure tools own their clicks; Place pins turns them off", async ({ page }) => {
  await api("PUT", `/api/designs/${id}/selection`, { items: [] });
  for (const pin of (await state()).pins) await api("DELETE", `/api/designs/${id}/pins/${pin.id}`);
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await expect(page.locator(".tcv_button_select")).toBeHidden(); // hitlcad's selection replaces the viewer's

  for (const tool of ["distance", "properties"]) {
    await page.locator(`.tcv_button_${tool}`).click();
    await clickModel(page, 0.5, 0.42);
    await clickModel(page, 0.45, 0.6);
    await expect(page.locator(".tcv_cad_tools, .cad-stage").getByText(tool === "distance" ? "Distance" : "Properties").first()).toBeVisible();
    await page.locator(`.tcv_button_${tool}`).click();
  }
  await expect(page.locator(".selection-box")).toBeHidden();
  expect((await state()).selection).toEqual([]);

  await page.locator(".tcv_button_distance").click();
  await page.locator(".pin-toggle").click(); // turns the distance tool off: the click places a pin
  await clickModel(page, 0.5, 0.42);
  await expect.poll(async () => (await state()).pins.length).toBe(1);
});

test("the grid popup stays open and each plane can be toggled with its label and checkbox", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const button = page.locator(".tcv_button_grid");
  const menu = page.locator(".tcv_grid-content");

  for (const plane of ["xy", "xz", "yz"]) {
    const checkbox = menu.locator(`.tcv_grid-${plane}`);
    await button.hover();
    await expect(menu).toBeVisible();
    const checked = await checkbox.isChecked();
    const label = menu.locator("label", { hasText: plane });
    const box = await label.boundingBox();
    if (!box) throw new Error(`no grid label for ${plane}`);
    // Move through the popup as a person would: a forced click would hide overflow/hit-testing bugs.
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 10 });
    await expect(menu).toBeVisible();
    await label.click();
    await expect(checkbox).toBeChecked({ checked: !checked });
    await checkbox.click();
    await expect(checkbox).toBeChecked({ checked });
  }
});

test("every viewer toolbar button and tab works without errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  // The measure tools change nothing until the model is clicked: the test above covers them.
  const buttons = (await page.locator(".cad-stage input.tcv_btn[class*=tcv_button_]:visible").evaluateAll((els) =>
    els.map((e) => [...e.classList].find((c) => c.startsWith("tcv_button_"))!),
  )).filter((b) => !["tcv_button_distance", "tcv_button_properties"].includes(b));
  expect(buttons.length).toBeGreaterThan(15);
  for (const button of buttons) {
    await page.locator(`.${button}`).click();
    await page.locator(`.${button}`).click({ force: true }); // back off (toggles), or harmless (views)
    if (await page.locator(".tcv_cad_help").isVisible()) await page.keyboard.press("Escape");
  }
  for (const name of ["clip", "zebra", "material", "tree"]) await page.locator(`.tcv_tab_${name}`).click();
  expect(errors).toEqual([]);
});

test("explode moves the parts of a design whose part is placed twice (box[0], box[1])", async ({ page }) => {
  const twice = path.join(WORK, "project", "designs", "twice");
  const { id: twiceId } = await api<{ id: string }>("POST", "/api/designs", { path: twice, create: { name: "Twice", backend: "build123d" } });
  writeDesign(twice, { parameters: [{ name: "width", value: 30 }, { name: "height", value: 10 }], ground: "box",
    parts: { box: { role: "a box", file: "parts/box.py", material: "PLA" } } },
  { "parts/box.py": MODEL, "assembly.py": ASSEMBLY.replace("[Pos()]", "[Pos(-30, 0, 0), Pos(30, 0, 0)]") });
  await api("POST", `/api/designs/${twiceId}/build`);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await openPanel(page, { name: "design", id: twiceId });
  await viewerReady(page);
  await expect(page.locator(".tcv_cad_tree")).toContainText("box[1]");
  // The model alone: the screenshots compare where its parts are, not the controls over it.
  await page.addStyleTag({ content: ".tcv_cad_toolbar, .tcv_cad_navigation, .viewport-hint, .side { visibility: hidden !important; }" });
  const canvas = page.locator(".cad-stage canvas").first();
  const before = await canvas.screenshot();
  await page.locator(".tcv_button_explode").dispatchEvent("click");
  await page.waitForTimeout(800); // it eases out over a few frames, then stays
  const apart = await canvas.screenshot();
  expect(apart.equals(before), "the parts moved apart").toBe(false);
  await page.waitForTimeout(500);
  expect((await canvas.screenshot()).equals(apart), "and stay apart").toBe(true);
  await page.getByLabel("How far apart").fill("3");
  await page.waitForTimeout(800);
  expect((await canvas.screenshot()).equals(apart), "further apart").toBe(false);
  await page.locator(".tcv_button_explode").dispatchEvent("click");
  await page.waitForTimeout(800);
  await expect(page.locator(".explode-spread")).toHaveCount(0);
  expect((await canvas.screenshot()).equals(before), "back together as built").toBe(true);
  expect(errors).toEqual([]);
});

test("explode is off for a model of one shape: there is nothing to pull apart", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await expect(page.locator(".tcv_button_explode")).toBeHidden();
});

test("every viewer control can be clicked at any editor width: nothing floats over it", async ({ page }) => {
  for (const width of [1280, 800, 640]) {
    await page.setViewportSize({ width, height: 700 });
    await openPanel(page, { name: "design", id });
    await viewerReady(page);
    const covered = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>(".cad-stage input.tcv_btn, .tcv_shape_filter, .stage-actions button, .review-tab, .inspection-tools summary")]
        .filter((e) => e.offsetParent !== null)
        .flatMap((e) => {
          const b = e.getBoundingClientRect();
          const top = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
          return top && (top === e || e.contains(top)) ? [] : [`${e.className} under ${top?.className}`];
        }),
    );
    expect(covered, `${width}px`).toEqual([]);
  }
});

