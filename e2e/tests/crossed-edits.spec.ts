import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, openPanel, WORK, writeDesign } from "../helpers";

/** An edit made on an earlier version merges with what was written since; two that changed the same value at once
 * are both kept, by rule, and the Design view says so until dismissed. */

const MODEL = `from build123d import *


def build(p, i):
    return {"box": (Box(p["width"], 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)), {})}
`;
const ASSEMBLY = `from build123d import Pos


def build(p, i):
    return {"box": [Pos()]}
`;

test("a panel edit on an earlier version keeps an agent's write, and crossing it is shown", async ({ page }) => {
  const folder = path.join(WORK, "project", "designs", "crossed");
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Crossed", backend: "build123d" } });
  writeDesign(folder, { parameters: [{ name: "width", value: 30, min: 1, max: 9999 }], ground: "box",
    parts: { box: { role: "a box", file: "parts/box.py", material: "PLA" } } }, { "parts/box.py": MODEL, "assembly.py": ASSEMBLY });
  const shown = await api<{ version: number }>("POST", `/api/designs/${id}/build`);

  const file = path.join(folder, "design.json");
  fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace('"name": "Crossed"', '"name": "Renamed"'));  // an agent's write
  await api("POST", `/api/designs/${id}/parameters`, { values: { width: 40 }, base: shown.version });
  const merged = JSON.parse(fs.readFileSync(file, "utf8"));
  expect([merged.name, merged.parameters[0].value]).toEqual(["Renamed", 40]);

  const now = await api<{ version: number }>("POST", `/api/designs/${id}/build`);
  fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace('"value": 40', '"value": 45'));  // the agent again
  await api("POST", `/api/designs/${id}/parameters`, { values: { width: 50 }, base: now.version });  // made before it

  await openPanel(page, { name: "inspector", id });
  const notice = page.getByRole("status", { name: "Edits that crossed" });
  await expect(notice).toContainText("design.json line");
  await expect(notice).toContainText("you, in the panel");  // whichever of the two was merged in last, with the other
  await expect(notice).toContainText("a save to the folder");
  await notice.getByRole("button", { name: "Dismiss" }).click();
  await expect(notice).toHaveCount(0);
});
