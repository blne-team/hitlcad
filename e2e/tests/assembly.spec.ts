import { expect, test } from "@playwright/test";
import { api, clickModel, fixtures, nothingBlocked, openPanel, viewerReady } from "../helpers";

test("an assembly shows its instances by name, in the viewer, the report and the structure", async ({ page }) => {
  const { assemblyId } = fixtures();
  await openPanel(page, { name: "design", id: assemblyId });
  await viewerReady(page);
  await expect(page.locator(".tcv_cad_tree")).toContainText("base");
  await expect(page.locator(".tcv_cad_tree")).toContainText("lid");

  // The part under the cursor is named beside it; a click selects it whole, a click on it again the face there.
  await clickModel(page, 0.5, 0.42); // the lid's top
  await expect(page.locator(".cad-hover-name")).toHaveText(/^(lid|base)( · \w+)?$/);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  await expect(page.locator(".selection-box li").first()).toContainText(/^1Whole part, volume .* \(instance (lid|base)\) at/);
  const { selection } = await api<{ selection: { kind: string; component: { topo: string; index: number } }[] }>("GET", `/api/designs/${assemblyId}`);
  expect(selection.map((s) => [s.kind, s.component.topo, s.component.index])).toEqual([["solid", "solid", 0]]);
  await clickModel(page, 0.5, 0.42);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  await expect(page.locator(".selection-box li").first()).toContainText(/of (lid|base)\) at/);

  // Pins are placed on parts the same way: a pin on a part not selected whole is on the whole part.
  await page.locator(".pin-toggle").click();
  await clickModel(page, 0.5, 0.42);
  await expect
    .poll(async () => (await api<{ pins: { component: { topo: string } | null; label: string }[] }>("GET", `/api/designs/${assemblyId}`)).pins.map((p) => [p.component?.topo, p.label.split(",")[0]]))
    .toEqual([["solid", "Whole part"]]);
  await page.locator(".pin-toggle").click();

  await openPanel(page, { name: "inspector", id: assemblyId });
  await page.locator(".tab", { hasText: "Structure" }).click();
  await expect(page.locator(".structure .node", { hasText: "lid" })).toBeVisible();
  await expect(page.locator(".structure .edge-mate")).toHaveClass(/st-pass/);
  await page.locator(".tab", { hasText: "Results" }).click();
  await expect(page.locator("h3", { hasText: "Parts, 2" })).toBeVisible();
  await expect(page.locator(".metrics th", { hasText: "lid" })).toBeVisible();
  const preview = page.locator("figure.preview img"); // the server's render, loaded across origins
  await expect(preview).toBeVisible();
  await expect.poll(() => preview.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await nothingBlocked(page);
});
