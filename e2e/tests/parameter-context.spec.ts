import * as path from "node:path";
import { expect, test } from "@playwright/test";
import type { Revision } from "../../vscode-extension/ui/src/api/types";
import { parametersForInstances } from "../../vscode-extension/ui/src/lib/parameters";
import { api, clickModel, openPanel, viewerReady, WORK, writeDesign } from "../helpers";

let id: string;
let revision: number;
const selection = (instances: string[], rev = revision) => api("PUT", `/api/designs/${id}/selection`, {
  revision: rev, items: instances.map((instance) => ({ point: [0, 0, 0], component: { instance, topo: "solid" } })),
});

test.beforeAll(async () => {
  const folder = path.join(WORK, "project", "designs", "parameter-context");
  ({ id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Parameter context", backend: "build123d" } }));
  writeDesign(folder, {
    parameters: [
      { name: "base_width", label: "Base width", value: 40, min: 10, max: 80 },
      { name: "lid_width", label: "Lid width", value: 40, min: 10, max: 80 },
      { name: "depth", label: "Depth", value: 30, min: 10, max: 80 },
      { name: "lid_z", label: "Lid position", value: 6.5, min: 6, max: 20 },
      { name: "unused", label: "Unused input", value: 5 },
    ],
    ground: "base",
    requirements: [{ text: "Lid width limit", check: { kind: "size", target: "lid", axis: "x", max: 1 } }],
    parts: {
      base: { role: "base", file: "parts/base.py", material: "PLA" },
      lid: { role: "lid", file: "parts/lid.py", material: "PLA" },
    },
  }, {
    "parts/base.py": `from build123d import *
def build(p, i):
    return {"base": (Box(p["base_width"], p["depth"], 6), {})}
`,
    "parts/lid.py": `from build123d import *
def build(p, i):
    return {"lid": (Box(i.lid_size, p["depth"], 2), {})}
`,
    "interfaces.py": `def raw_width(p, i):
    return p["lid_width"]
def lid_size(p, i):
    return i.raw_width
`,
    "assembly.py": `from build123d import Pos
def build(p, i):
    return {"base": [Pos()], "lid": [Pos(0, 0, p["lid_z"])]}
`,
  });
  ({ id: revision } = await api<{ id: number }>("POST", `/api/designs/${id}/build`));
});

test("the icon opens inspection with keyboard and closes without clearing selection", async ({ page }) => {
  await selection(["lid"]);
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const inspect = page.getByRole("button", { name: "Inspect model", exact: true });
  await expect(inspect.locator("svg")).toBeVisible();
  await expect(inspect).toHaveText("");
  await inspect.focus();
  await inspect.press("Enter");
  const controls = page.getByRole("toolbar", { name: "Inspect model", exact: true });
  await expect(controls).toBeVisible();
  await controls.getByRole("button", { name: "Isolate", exact: true }).focus();
  await page.keyboard.press("Escape");
  await expect(controls).toBeHidden();
  await expect(inspect).toBeFocused();
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  await inspect.click();
  await page.locator(".stage-title h1").click();
  await expect(controls).toBeHidden();
});

test("Inspect enables model selection after measuring or placing pins", async ({ page }) => {
  await selection([]);
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  for (const tool of [null, "distance", "properties"]) {
    if (tool) await page.locator(`.tcv_button_${tool}`).click();
    await page.getByRole("button", { name: "Inspect model", exact: true }).click();
    await clickModel(page, 0.5, 0.42);
    await expect(page.locator(".selection-box li"), `selection after ${tool ?? "opening Inspect"}`).toHaveCount(1);
    await page.locator(".selection-box").getByRole("button", { name: "Clear", exact: true }).click();
  }
  const before = await api<{ pins: unknown[] }>("GET", `/api/designs/${id}`);
  await page.getByRole("button", { name: "Place pins", exact: true }).click();
  await page.getByRole("button", { name: "Inspect model", exact: true }).click();
  await expect(page.getByRole("button", { name: "Place pins", exact: true })).toHaveAttribute("aria-pressed", "false");
  await clickModel(page, 0.5, 0.42);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  expect((await api<{ pins: unknown[] }>("GET", `/api/designs/${id}`)).pins).toHaveLength(before.pins.length);
});

test("editing follows selected parts, preserves drafts and exposes shared design scope", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await selection(["lid"]);
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await page.getByRole("button", { name: "Inspect model", exact: true }).click();
  await page.getByRole("button", { name: "Edit parameters", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Parameters", exact: true })).toHaveAttribute("aria-selected", "true");
  const context = page.getByRole("region", { name: "Parameter context" });
  await expect(context).toContainText("lid");
  await expect(context).toContainText(`Revision ${revision}`);
  await expect(context).toContainText("Shared inputs may also change other parts.");
  await expect(page.getByLabel("Lid width", { exact: true })).toBeVisible(); // through two interface reads
  await expect(page.getByLabel("Depth", { exact: true })).toBeVisible(); // shared input
  await expect(page.getByLabel("Lid position", { exact: true })).toBeVisible(); // placement input
  await expect(page.getByLabel("Base width", { exact: true })).toHaveCount(0);
  await page.getByLabel("Lid width", { exact: true }).fill("44");
  await page.screenshot({ path: path.join(WORK, "parameters-selected.png") });

  // A fresh selection supersedes the inspection, as an actual model click does.
  await page.evaluate(() => window.postMessage({ type: "hitlcad.ui.state", state: { inspection: null } }, "*"));
  await selection(["base"]);
  await expect(context.locator(".parameter-targets")).toHaveText("base");
  await expect(page.getByLabel("Base width", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Lid width", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "outside this selection" })).toContainText("1 edited parameter is outside this selection");
  await page.getByRole("button", { name: "All parameters", exact: true }).click();
  await expect(page.getByLabel("Lid width", { exact: true })).toHaveValue("44");
  await expect(page.getByLabel("Unused input", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "For selection", exact: true }).click();

  await selection(["lid", "base"]);
  await expect(context.locator(".parameter-targets strong")).toHaveCount(2);
  await expect(page.getByLabel("Lid width", { exact: true })).toHaveValue("44");
  await expect(page.getByLabel("Base width", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Depth", { exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Locate in model", exact: true }).click();
  await page.locator(".selection-box").getByRole("button", { name: "Clear", exact: true }).click();
  await expect(context).toContainText("Whole design");
  await expect(page.getByLabel("Unused input", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.getByLabel("Lid width", { exact: true })).toHaveValue("40");
  expect(errors).toEqual([]);
});

test("the Design view and dark narrow panel show the same revision-aware context", async ({ page }) => {
  await selection(["lid"]);
  await openPanel(page, { name: "inspector", id });
  await page.getByRole("tab", { name: "Parameters", exact: true }).click();
  const context = page.getByRole("region", { name: "Parameter context" });
  await expect(context.locator(".parameter-targets")).toHaveText("lid");
  await page.setViewportSize({ width: 360, height: 700 });
  await page.evaluate(() => document.body.dataset.vscodeThemeKind = "vscode-dark");
  await expect(page.getByLabel("Lid width", { exact: true })).toBeVisible();
  expect(await page.locator(".params").evaluate((panel) => {
    const box = panel.getBoundingClientRect();
    return box.left >= 0 && box.right <= window.innerWidth && panel.scrollWidth <= panel.clientWidth;
  })).toBe(true);
  await page.screenshot({ path: path.join(WORK, "parameters-dark-narrow.png") });
  await page.evaluate((rev) => window.postMessage({ type: "hitlcad.ui.state", state: {
    tab: "parameters",
    inspection: { revision: rev - 1, instances: ["base"], action: "focus", sequence: 1 },
  } }, "*"), revision);
  await expect(context.locator(".parameter-targets")).toHaveText("lid");
  await selection([]);
  await expect(context).toContainText("Whole design");
});

test("inspecting a finding supplies the parameter context without a geometry selection", async ({ page }) => {
  await selection([]);
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await page.getByRole("button", { name: "Inspect model", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit parameters", exact: true })).toBeDisabled();
  await page.locator(".tcv_button_distance").click();
  await page.getByRole("tab", { name: /Results/ }).click();
  await page.locator(".problem", { hasText: "Lid width limit" }).getByRole("button", { name: "Inspect in model", exact: true }).click();
  await page.getByRole("tab", { name: "Parameters", exact: true }).click();
  await expect(page.locator(".parameter-targets")).toHaveText("lid");
  await expect(page.getByLabel("Lid width", { exact: true })).toBeVisible();
  await expect(page.locator(".selection-box")).toBeHidden();
  await page.getByRole("tab", { name: "Parameters", exact: true }).click();
  await clickModel(page, 0.5, 0.42);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
});

test("nested components map their recorded inputs through bindings to parent parameters", async () => {
  const built = await api<Revision>("GET", `/api/designs/${id}/revisions/${revision}`);
  expect([...parametersForInstances(built, ["lid"])]).toEqual(expect.arrayContaining(["lid_width", "depth", "lid_z"]));
  expect(parametersForInstances(built, ["lid"]).has("base_width")).toBe(false);
  expect(parametersForInstances(built, ["missing"]).size).toBe(0);
  const nested = structuredClone(built);
  const spec = nested.spec!;
  nested.report.parts = [];
  nested.report.assembly_reads = { parameters: [], interfaces: [], modules: [] };
  nested.report.components = { child: built.report };
  nested.report.instances = [{ path: "child[2]", part: null, component: "child", transform: built.report.instances[0]!.transform }];
  spec.components = { child: built.spec! };
  spec.design.components = { child: { design: "../child", configuration: null, flexible: false, bind: { lid_width: "base_width", depth: "interfaces.lid_size" } } };
  const needed = parametersForInstances(nested, ["child[2]/lid"]);
  expect([...needed].sort()).toEqual(["base_width", "lid_width"]); // unbound child placement stays local
});
