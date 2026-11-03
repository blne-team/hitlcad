import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, openPanel, viewerReady, WORK, writeDesign } from "../helpers";

/** Preparing to print: the human enters their printer once, the project keeps it, and each printed part is checked
 * against its bed lying as it prints. */

const MODEL = `from build123d import *


def build(p, i):
    return {"rail": (Box(300, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)), {})}
`;
const ASSEMBLY = `from build123d import Pos


def build(p, i):
    return {"rail": [Pos(), Pos(0, 40, 0)]}
`;

test("the Print tab asks for the printer once, keeps it in the project, and checks each printed part on its bed", async ({ page }) => {
  const project = path.join(WORK, "printing");
  const folder = path.join(project, "designs", "rail");
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Rail", backend: "build123d" } });
  fs.writeFileSync(path.join(project, "hitlcad.toml"), "# my printers\n[plugins]\n");
  writeDesign(folder, { ground: "rail", manufacturing: { process: "fdm" }, parts: { rail: { role: "a rail", file: "parts/rail.py", material: "PLA" } } },
    { "parts/rail.py": MODEL, "assembly.py": ASSEMBLY });
  await api("POST", `/api/designs/${id}/build`);

  await openPanel(page, { name: "inspector", id });
  await page.locator(".tab", { hasText: "Print" }).click();
  const form = page.locator(".printer-form");
  await expect(form).toContainText("Which printer will you print this on?");
  await form.getByLabel("Printer").fill("Bambu Lab P2S");
  await form.getByLabel("PLA filament profile").fill("Bambu PLA Basic");
  await form.getByLabel("PLA AMS slot").fill("1");
  await form.getByRole("button", { name: "Save printer" }).click();

  await expect(page.locator(".printer-summary")).toContainText("Bambu Lab P2S: 256 × 256 × 256 mm, 0.4 mm nozzle, sliced in Bambu Studio.");
  const kept = fs.readFileSync(path.join(project, "hitlcad.toml"), "utf8");
  expect(kept).toContain("# my printers");
  expect(kept).toMatch(/\[printer\.materials\.PLA\]\s+profile = "Bambu PLA Basic"\s+slot = 1/);
  // The printer rebuilt the design: the 300 mm rail fits the 256 mm bed only turned across it
  const rail = page.locator(".print-parts tr", { hasText: "rail" });
  await expect(rail).toContainText("as modelled");
  await expect(rail.locator("td").first()).toHaveText("2"); // placed twice: printed twice, listed once
  await expect(page.locator(".print")).toContainText("2 printed pieces of 1 part");
  await expect(rail.locator(".print-pass").first()).toHaveText("fits");
  await expect(rail.locator(".print-pass").first()).toHaveAttribute("title", /turned -?\d+° on the bed/);
  // Its settings, recommended (nothing in its manufacturing decides them), each with its reason
  const settings = page.locator(".print-settings").first();
  await expect(settings).toContainText("0.2 mm layers");
  await expect(settings.locator(".print-recommended", { hasText: "0.2 mm layers" })).toHaveAttribute("title", /^Recommended: half the 0\.4 mm nozzle/);
  await expect(settings).toContainText("no supports");

  // Print turns the main view into the print view: each printed part once, lying as it prints, with its plan beside
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  await page.getByRole("button", { name: "Print", exact: true }).click();
  await expect(page.getByRole("button", { name: "Print", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".tcv_cad_tree")).toContainText("print"); // the print view's scene, not the assembly's
  const plan = page.locator(".print-plan-box");
  await expect(plan).toContainText("2 pieces of 1 part");
  await expect(plan.locator("li").first()).toContainText("rail ×2 · PLA · as modelled");
  await expect(plan.locator(".print-recommended", { hasText: "0.2 mm layers" })).toBeVisible();
  await expect(page.locator(".selection-box:not(.print-plan-box)")).toHaveCount(0);
  await page.getByRole("button", { name: "Print", exact: true }).click(); // back to the assembly
  await expect(plan).toBeHidden();
});
