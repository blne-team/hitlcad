import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, clickModel, fixtures, nothingBlocked, openPanel, viewerReady } from "../helpers";

test("an assembly shows its parts by name, in the viewer, the report and build/", async ({ page }) => {
  const { assemblyId, assemblyFolder } = fixtures();
  await openPanel(page, { name: "design", id: assemblyId });
  await viewerReady(page);
  await expect(page.locator(".tcv_cad_tree")).toContainText("base");
  await expect(page.locator(".tcv_cad_tree")).toContainText("lid");

  await clickModel(page, 0.5, 0.42); // the lid's top face
  await expect(page.locator(".selection-box li").first()).toContainText(/of (lid|base)\) at/);

  // With the filter on Solid, the same click picks the whole part.
  await page.keyboard.press("Escape");
  await page.locator(".tcv_shape_filter").click();
  await page.locator(".tcv_shape_filter").getByText("Solid", { exact: true }).click();
  await clickModel(page, 0.5, 0.42);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  await expect(page.locator(".selection-box li").first()).toContainText(/^1Whole part, volume .* \(part (lid|base)\) at/);
  const { selection } = await api<{ selection: { kind: string; component: { topo: string; index: number } }[] }>("GET", `/api/designs/${assemblyId}`);
  expect(selection.map((s) => [s.kind, s.component.topo, s.component.index])).toEqual([["solid", "solid", 0]]);

  // The filter applies to pins too: a pin placed under Solid is on the whole part.
  await page.locator(".pin-toggle").click();
  await clickModel(page, 0.5, 0.42);
  await expect
    .poll(async () => (await api<{ pins: { component: { topo: string } | null; label: string }[] }>("GET", `/api/designs/${assemblyId}`)).pins.map((p) => [p.component?.topo, p.label.split(",")[0]]))
    .toEqual([["solid", "Whole part"]]);
  await page.locator(".pin-toggle").click();

  await openPanel(page, { name: "inspector", id: assemblyId });
  await page.locator(".tab", { hasText: "Checks" }).click();
  await expect(page.locator("h3", { hasText: "Parts, 2" })).toBeVisible();
  await expect(page.locator(".metrics th", { hasText: "lid" })).toBeVisible();
  const preview = page.locator("figure.preview img"); // the server's render, loaded across origins
  await expect(preview).toBeVisible();
  await expect.poll(() => preview.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await nothingBlocked(page);

  for (const file of ["part-base.step", "part-base.stl", "part-lid.step", "part-lid.stl"]) {
    expect(fs.existsSync(path.join(assemblyFolder, "build", file)), file).toBe(true);
  }
});
