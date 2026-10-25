import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, openPanel, viewerReady, WORK } from "../helpers";

// One hinge for both tests, in order: the first on revision 1, the second builds revision 2 with its lid fixed.
const folder = path.join(WORK, "project", "designs", "hinge");
let id = "";

test.beforeAll(async () => {
  fs.cpSync(path.resolve(__dirname, "../../backend/tests/designs/hinge"), folder, { recursive: true });
  ({ id } = await api<{ id: string }>("POST", "/api/designs", { path: folder }));
  await api("POST", `/api/designs/${id}/build`);
});

test("a mate's range of motion moves its side in the 3D view", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);

  const control = page.locator(".motion-control");
  await expect(control).toContainText("base.hinge ↔ lid.pin");
  const canvas = page.locator(".cad-stage canvas").first();
  const closed = await canvas.screenshot();
  await control.getByLabel("Move along the mate").fill("-90");
  await expect(control).toContainText("-90.0°");
  await page.waitForTimeout(300);
  expect((await canvas.screenshot()).equals(closed)).toBe(false); // the lid swung open
  await control.getByRole("button", { name: "As built" }).click();
  await page.waitForTimeout(300);
  expect((await canvas.screenshot()).equals(closed)).toBe(true);
});

test("another revision shows its parts as built, not the motion left on the one before", async ({ page }) => {
  // Revision 2 fixes the lid: a rigid mate has no range of motion.
  const file = path.join(folder, "design.json");
  const design = JSON.parse(fs.readFileSync(file, "utf8")) as { mates: { type: string; limits?: unknown }[] };
  design.mates = design.mates.map(({ limits: _, ...mate }) => ({ ...mate, type: "rigid" }));
  fs.writeFileSync(file, JSON.stringify(design, null, 2));
  await api("POST", `/api/designs/${id}/build`);
  await openPanel(page, { name: "design", id });
  await viewerReady(page);

  const control = page.locator(".motion-control");
  await expect(control).toHaveCount(0);
  const canvas = page.locator(".cad-stage canvas").first();
  const fixed = await canvas.screenshot();
  await page.getByRole("button", { name: "Previous revision" }).click();
  await control.getByLabel("Move along the mate").fill("-90");
  await page.getByRole("button", { name: "Next revision" }).click();
  await expect(control).toHaveCount(0);
  await viewerReady(page);
  expect((await canvas.screenshot()).equals(fixed)).toBe(true);
});
