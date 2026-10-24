import * as fs from "node:fs";
import * as path from "node:path";
import { startHarness } from "./harness";
import { api, WORK, writeFixtures } from "./helpers";

interface Design {
  id: string;
}

const repo = path.resolve(__dirname, "..");

// The fixture part, written as an agent would: a 40 × 30 × 10 mm rounded block with tunable dimensions.
const MODEL = `from build123d import *


def build(p):
    with BuildPart() as part:
        Box(p["width"], p["depth"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        fillet(part.edges().filter_by(Axis.Z), radius=p["corner_radius"])
    return part
`;
const PARAMETERS = [
  { name: "width", label: "Width", value: 40, min: 1, max: 500 },
  { name: "depth", label: "Depth", value: 30, min: 1, max: 500 },
  { name: "height", label: "Height", value: 10, min: 1, max: 500 },
  { name: "corner_radius", label: "Corner radius", value: 3, min: 0.1, max: 50 },
];

const ASSEMBLY = `from build123d import *


def build(p):
    base = Box(40, 30, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    lid = Pos(0, 0, 6.5) * Box(40, 30, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"base": base, "lid": lid}
`;

/** Build the fixture designs the specs share (a block with a taller second revision, and an assembly), and serve the panel UI. */
export default async function globalSetup() {
  for (const built of ["vscode-extension/out/ui/panel.js", "vscode-extension/out/designUi.js"]) {
    if (!fs.existsSync(path.join(repo, built))) throw new Error(`${built} is missing: run \`npm --prefix vscode-extension run compile\` first`);
  }

  const folder = path.join(WORK, "project", "designs", "block");
  const design = await api<Design>("POST", "/api/designs", { path: folder, create: { name: "Block", backend: "build123d" } });
  const designFile = path.join(folder, "design.json");
  fs.writeFileSync(designFile, JSON.stringify({ ...JSON.parse(fs.readFileSync(designFile, "utf8")), parameters: PARAMETERS }, null, 2));
  fs.writeFileSync(path.join(folder, "model.py"), MODEL);
  await api("POST", `/api/designs/${design.id}/build`);
  await api("POST", `/api/designs/${design.id}/parameters`, { values: { height: 20 } });

  const assemblyFolder = path.join(WORK, "project", "designs", "pair");
  const assembly = await api<Design>("POST", "/api/designs", { path: assemblyFolder, create: { name: "Pair", backend: "build123d" } });
  fs.writeFileSync(path.join(assemblyFolder, "model.py"), ASSEMBLY);
  await api("POST", `/api/designs/${assembly.id}/build`);

  const revision1 = path.join(WORK, "data", "designs", design.id, "revisions", "1");
  writeFixtures({
    designId: design.id,
    step: path.join(revision1, "model.step"),
    stl: path.join(revision1, "mesh.stl"),
    assemblyId: assembly.id,
    assemblyFolder,
  });
  return startHarness();
}
