import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, openPanel, WORK, writeDesign } from "../helpers";

/** Undo takes back the human's panel edit where it was, keeping what an agent wrote since; Redo makes it again. */

const MODEL = `from build123d import *


def build(p, i):
    return {"box": (Box(p["width"], 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)), {})}
`;
const ASSEMBLY = `from build123d import Pos


def build(p, i):
    return {"box": [Pos()]}
`;

test("undo and redo in the panel take back the human's edit and keep the agent's", async ({ page }) => {
  const folder = path.join(WORK, "project", "designs", "undo");
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Undo", backend: "build123d" } });
  writeDesign(folder, { parameters: [{ name: "width", value: 30, min: 1, max: 9999 }], ground: "box",
    parts: { box: { role: "a box", file: "parts/box.py", material: "PLA" } } }, { "parts/box.py": MODEL, "assembly.py": ASSEMBLY });
  const shown = await api<{ version: number }>("POST", `/api/designs/${id}/build`);
  await api("POST", `/api/designs/${id}/parameters`, { values: { width: 40 }, base: shown.version });
  const file = path.join(folder, "design.json");
  fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace('"name": "Undo"', '"name": "Renamed"'));  // an agent's write
  const read = () => JSON.parse(fs.readFileSync(file, "utf8")) as { name: string; parameters: { value: number }[] };

  await openPanel(page, { name: "design", id });
  const undo = page.getByRole("button", { name: "Undo" });
  const redo = page.getByRole("button", { name: "Redo" });
  await expect(undo).toHaveAttribute("title", /Undo Parameter edit: width=40/);
  await expect(redo).toBeDisabled();
  await undo.click();
  await expect.poll(() => read().parameters[0]!.value).toBe(30);
  expect(read().name).toBe("Renamed");
  await expect(undo).toBeDisabled();

  await page.keyboard.press("Control+Shift+Z");
  await expect.poll(() => read().parameters[0]!.value).toBe(40);
  expect(read().name).toBe("Renamed");
  await expect(redo).toBeDisabled();
  await expect(undo).toBeEnabled();
});
