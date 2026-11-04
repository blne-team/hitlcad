import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, clickModel, openPanel, viewerReady, WORK, writeDesign } from "../helpers";

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
  const rail = page.locator(".print-plan .plan-row", { hasText: "rail" });
  await expect(rail).toHaveText("rail ×2 · PLA · as modelled"); // placed twice: listed once
  await rail.click();
  const item = page.locator(".plan-item");
  await expect(item.locator(".plan-head")).toHaveText("rail ×2 · PLA · as modelled · fits the bed");
  // Its settings, recommended (nothing in its manufacturing decides them), each with its reason
  await expect(item.locator(".plan-settings li").first()).toContainText(/^0\.2 mm layers recommended: half the 0\.4 mm nozzle/);
  await expect(item.locator(".plan-settings")).toContainText("no supports");
  // The file to print from is an output: named where to choose it, then offered once the design writes it
  await expect(page.locator(".print")).toContainText("choose For the slicer among the build's outputs");
  writeDesign(folder, { outputs: ["print"] }, {});
  const files = page.locator(".slicer-file");
  await expect(files).toContainText("Open print.3mf and print-plan.md from the design's build/ folder: open the 3MF in Bambu Studio as a project");

  // The side panel's Print tab turns the main view into the print view: each printed part once, lying as it prints,
  // its plan beside it; folding the tab returns to the assembly
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const side = page.locator(".review");
  const tab = side.getByRole("tab", { name: "Print" });
  await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".tcv_cad_tree")).toContainText("print"); // the print view's scene, not the assembly's
  await expect(side).toContainText("2 pieces of 1 part");
  await side.locator(".plan-row", { hasText: "rail" }).click();
  await expect(side.locator(".plan-head")).toContainText("rail ×2 · PLA · as modelled");
  await side.getByRole("button", { name: "‹ All 1 printed parts" }).click();
  await expect(side.locator(".plan-row")).toHaveCount(1);
  await expect(page.locator(".selection-box")).toHaveCount(0);
  await tab.click();
  await expect(side.locator(".print")).toBeHidden();
  await expect(page.locator(".tcv_cad_tree")).not.toContainText("print"); // back to the assembly
});

const BRACKET = `from build123d import *


def build(p, i):
    on = (Align.CENTER, Align.CENTER, Align.MIN)
    return {"bracket": (Pos(-18, 0, 0) * Box(4, 20, 40, align=on) + Pos(0, 0, 36) * Box(40, 20, 4, align=on), {})}
`;

test("a click on a part in the print view opens its plan, and printing it a better way writes its up", async ({ page }) => {
  const project = path.join(WORK, "bracket-project");
  const folder = path.join(project, "designs", "bracket");
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Bracket", backend: "build123d" } });
  fs.writeFileSync(path.join(project, "hitlcad.toml"),
    '[plugins]\n\n[printer]\nname = "Bambu Lab P2S"\nbuild_volume_mm = [256, 256, 256]\nnozzle_mm = 0.4\nslicer = "bambu-studio"\n');
  writeDesign(folder, { ground: "bracket", manufacturing: { process: "fdm" }, parts: { bracket: { role: "a post with an arm out at its top", file: "parts/bracket.py", material: "PLA" } } },
    { "parts/bracket.py": BRACKET, "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {\"bracket\": [Pos()]}\n" });
  await api("POST", `/api/designs/${id}/build`);

  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const side = page.locator(".review");
  await side.getByRole("tab", { name: "Print" }).click();
  await expect(page.locator(".tcv_cad_tree")).toContainText("print");
  await viewerReady(page); // the print view's own scene
  // Shaded where it needs support (under its arm) and where it lies on the bed, as its process checks found
  await expect(side.locator(".shade-key")).toContainText("needs support");
  await clickModel(page, 0.5, 0.45); // its arm, in the middle of its bed
  await expect(page.locator(".cad-hover-name")).toHaveText("bracket ×1 · as modelled");
  const item = side.locator(".plan-item"); // the click chose it: its plan opens beside it
  await expect(item.locator(".plan-ways h4")).toHaveText(/^It could lie better\./);
  await expect(item.locator(".plan-ways li").first()).toContainText(/^best on its .* face: 0 mm² support/);
  // alone in its design, all of it shows: the support under its arm scars it, and the plan says so
  await expect(side.locator(".shade-key")).toContainText("shows, and needs support: scarred");
  await expect(item.locator(".plan-ways li", { hasText: "as modelled" })).toContainText(/as modelled: (\d+) mm² support \(\1 shows\)/);
  // Pointing at a way shows the part lying so, shaded as its process found it; pointing away, as it prints
  const best = item.locator(".plan-ways li").first();
  await best.hover();
  await expect(page.locator(".viewport-hint")).toContainText(/^Showing bracket on its .* face, as it would print\./);
  await page.mouse.move(5, 5);
  await expect(page.locator(".viewport-hint")).toContainText("Each printed part once, lying as it prints.");
  await item.getByRole("button", { name: "Print this way" }).first().click();
  // A panel edit: the part's own manufacturing turned, built again, and now it prints the best way up
  await expect.poll(() => JSON.parse(fs.readFileSync(path.join(folder, "design.json"), "utf8")).parts.bracket.manufacturing?.up).toBeDefined();
  await expect(item.locator(".plan-ways h4")).toHaveText(/^It prints the best way up\./, { timeout: 60_000 });
});

const BLOCK = `from build123d import *


def build(p, i):
    on = (Align.CENTER, Align.CENTER, Align.MIN)
    return {"block": (Box(40, 20, 40, align=on) - Pos(0, 0, 20) * Rotation(90, 0, 0) * Cylinder(10, 20), {})}
`;

test("a round hole lying on its side is a teardrop finding, shaded where it needs support, in the plan's reasons", async ({ page }) => {
  const project = path.join(WORK, "block-project");
  const folder = path.join(project, "designs", "block");
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Block", backend: "build123d" } });
  fs.writeFileSync(path.join(project, "hitlcad.toml"),
    '[plugins]\n\n[printer]\nname = "Bambu Lab P2S"\nbuild_volume_mm = [256, 256, 256]\nnozzle_mm = 0.4\nslicer = "bambu-studio"\n');
  writeDesign(folder, { ground: "block", manufacturing: { process: "fdm" }, parts: { block: { role: "a block with a Ø20 bore across it", file: "parts/block.py", material: "PLA" } } },
    { "parts/block.py": BLOCK, "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {\"block\": [Pos()]}\n" });
  await api("POST", `/api/designs/${id}/build`);

  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const side = page.locator(".review");
  await side.getByRole("tab", { name: "Results" }).click();
  await expect(side.locator(".report")).toContainText("1 round hole: Ø20.0 mm at (0.0, 0.0, 20.0)");
  await side.getByRole("tab", { name: "Print" }).click();
  await viewerReady(page);
  await side.locator(".plan-row", { hasText: "block" }).click();
  // Its top spans 14.1 mm, past a bridge: support is recommended for it, and the way it prints is shaded there
  await expect(side.locator(".plan-settings")).toContainText(/supports recommended: 1 round hole: Ø20\.0 mm .* on support\)/);
});
