import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, openPanel, viewerReady, WORK } from "../helpers";

test("a mate's range of motion moves its side in the 3D view", async ({ page }) => {
  const folder = path.join(WORK, "project", "designs", "hinge");
  fs.cpSync(path.resolve(__dirname, "../../backend/tests/designs/hinge"), folder, { recursive: true });
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder });
  await api("POST", `/api/designs/${id}/build`);
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
