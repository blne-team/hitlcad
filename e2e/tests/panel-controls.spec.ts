import * as fs from "node:fs";
import * as path from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { api, clickModel, openPanel, sentToExtension, viewerReady, WORK } from "../helpers";

/** Every control of the 3D editor tab and the Design view does what it says, on a design of its own. */

const MODEL = `from build123d import *


def build(p):
    return Box(p["width"], 20, p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
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
  const file = path.join(folder, "design.json");
  const parameters = [
    { name: "width", label: "Width", value: 30, min: 1, max: 100 },
    { name: "height", label: "Height", value: 10, min: 1, max: 100 },
  ];
  fs.writeFileSync(file, JSON.stringify({ ...JSON.parse(fs.readFileSync(file, "utf8")), parameters }, null, 2));
  fs.writeFileSync(path.join(folder, "model.py"), MODEL);
  await api("POST", `/api/designs/${id}/build`);
  await api("POST", `/api/designs/${id}/parameters`, { values: { height: 15 } });
});

const tab = (page: Page, name: string) => page.locator(".tab", { hasText: name }).click();

test("the Design view's tabs each show their panel", async ({ page }) => {
  await openPanel(page, { name: "inspector", id });
  for (const [name, shows] of [
    ["Parameters", page.getByRole("button", { name: "Rebuild with these values" })],
    ["Changes", page.locator(".changes-pick select")],
    ["Checks", page.locator("h3", { hasText: "Files" })],
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
  const onPart = { part: "/Group/model", topo: "solid" };
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

test("checks: every listed file is served", async ({ page, request }) => {
  await openPanel(page, { name: "inspector", id });
  await tab(page, "Checks");
  const links = await page.locator(".files a").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).href));
  expect(links.length).toBeGreaterThan(0);
  for (const href of links) expect((await request.get(href)).status(), href).toBe(200);
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
  const canvas = page.locator(".cad-stage canvas").first();
  const camera = ["reset", "resize", "iso", "front", "rear", "top", "bottom", "left", "right"].map((v) => `tcv_button_${v}`);
  for (const button of buttons) {
    if (camera.includes(button)) {
      // Move the camera first, so the button has something to change: zoom in and look from another side.
      await canvas.hover();
      await page.mouse.wheel(0, -400);
      await page.locator(button === "tcv_button_top" ? ".tcv_button_front" : ".tcv_button_top").click();
      await page.waitForTimeout(300);
    }
    const before = await canvas.screenshot();
    await page.locator(`.${button}`).click();
    await page.waitForTimeout(300);
    const changed = !(await canvas.screenshot()).equals(before) || (await page.locator(".tcv_cad_help").isVisible());
    expect(changed, `${button} changes the view`).toBe(true);
    await page.locator(`.${button}`).click({ force: true }); // back off (toggles), or harmless (views)
    if (await page.locator(".tcv_cad_help").isVisible()) await page.keyboard.press("Escape");
  }
  for (const name of ["clip", "zebra", "material", "tree"]) await page.locator(`.tcv_tab_${name}`).click();
  expect(errors).toEqual([]);
});
