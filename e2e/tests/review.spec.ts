import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, BASE_URL, fixtures, openPanel, viewerReady, WORK, writeDesign } from "../helpers";

let id: string;
let revision: number;

test.beforeAll(async () => {
  const folder = path.join(WORK, "project", "designs", "review");
  fs.cpSync(fixtures().assemblyFolder, folder, { recursive: true });
  writeDesign(folder, { name: "Review", requirements: [
    { text: "Lid thickness limit", check: { kind: "size", target: "lid", axis: "z", max: 1 } },
  ] }, {});
  ({ id } = await api<{ id: string }>("POST", "/api/designs", { path: folder }));
  ({ id: revision } = await api<{ id: number }>("POST", `/api/designs/${id}/build`));
});

test("a finding focuses its part; isolate, sections and restore operate on it", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await page.getByRole("tab", { name: /Results/ }).click();
  await page.locator(".problem", { hasText: "Lid thickness limit" }).getByRole("button", { name: "Inspect in model" }).click();
  await page.getByRole("tab", { name: /Results/ }).click();
  await page.getByRole("button", { name: "Inspect model", exact: true }).click();
  const controls = page.getByRole("toolbar", { name: "Inspect model" });
  await expect(controls).toContainText("1 part");
  await controls.getByRole("button", { name: "Isolate", exact: true }).click();
  await expect(page.locator('.tv-tree-node[data-path$="/base"]')).toHaveAttribute("data-state0", "0");
  await expect(page.locator('.tv-tree-node[data-path$="/lid"]')).toHaveAttribute("data-state0", "1");
  await page.screenshot({ path: path.join(WORK, "review-isolated.png") });
  for (const axis of ["X", "Y", "Z"]) {
    await controls.getByRole("button", { name: axis, exact: true }).click();
    await expect(page.locator(".tcv_tab_clip")).toHaveClass(/tcv_tab-selected/);
  }
  await page.screenshot({ path: path.join(WORK, "review-section.png") });
  await controls.getByRole("button", { name: "Show all", exact: true }).click();
  await expect(page.locator(".tcv_tab_tree")).toHaveClass(/tcv_tab-selected/);
  await expect(page.locator('.tv-tree-node[data-path$="/base"]')).toHaveAttribute("data-state0", "1");
  expect(errors).toEqual([]);
});

test("a selected pair is measured and its evidence reaches the agent through a pin", async ({ page }) => {
  await api("PUT", `/api/designs/${id}/selection`, { revision, items: [
    { point: [0, 0, 6] }, { point: [0, 0, 6.5] },
  ] });
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await page.getByRole("button", { name: "Measure selected pair" }).click();
  await expect(page.locator(".measurement-result")).toContainText("minimum distance 0.500 mm");
  await page.getByRole("textbox", { name: "Measurement note" }).fill("Increase this clearance to 0.8 mm.");
  await page.getByRole("button", { name: "Pin measurement", exact: true }).click();
  await expect(page.getByRole("button", { name: "Saved to pin" })).toBeVisible();
  const state = await api<{ pins: { note: string; revision: number }[] }>("GET", `/api/designs/${id}`);
  expect(state.pins.at(-1)?.revision).toBe(revision);
  expect(state.pins.at(-1)?.note).toContain(`Measured in revision ${revision}`);
  expect(state.pins.at(-1)?.note).toContain("Increase this clearance to 0.8 mm.");
  const context = await fetch(`${BASE_URL}/api/context?root=${encodeURIComponent(WORK)}`).then((r) => r.text());
  expect(context).toContain("minimum distance 0.500 mm");
});
