import * as fs from "node:fs";
import * as path from "node:path";
import { startHarness } from "./harness";
import { api, WORK, writeDesign, writeFixtures } from "./helpers";

interface Design {
  id: string;
}

const repo = path.resolve(__dirname, "..");

// The fixture part, written as an agent would: a 40 × 30 × 10 mm rounded block with tunable dimensions.
const BLOCK = `from build123d import *


def build(p, i):
    with BuildPart() as part:
        Box(p["width"], p["depth"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        fillet(part.edges().filter_by(Axis.Z), radius=p["corner_radius"])
    return {"block": (part.part, {})}
`;
const PLACE_ONE = (name: string) => `from build123d import Pos


def build(p, i):
    return {"${name}": [Pos()]}
`;
const PARAMETERS = [
  { name: "width", label: "Width", value: 40, min: 1, max: 500 },
  { name: "depth", label: "Depth", value: 30, min: 1, max: 500 },
  { name: "height", label: "Height", value: 10, min: 1, max: 500 },
  { name: "corner_radius", label: "Corner radius", value: 3, min: 0.1, max: 50 },
];

// A two-part assembly: a base, and a lid 0.5 mm above it (a rigid mate with an offset).
const PAIR = `from build123d import *


def build(p, i):
    base = Box(40, 30, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    lid = Box(40, 30, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"base": (base, {"top": Location((0, 0, 6))}), "lid": (lid, {"bottom": Location((0, 0, 0), (180, 0, 0))})}
`;
const PAIR_ASSEMBLY = `from build123d import Pos


def build(p, i):
    return {"base": [Pos()], "lid": [Pos(0, 0, 6.5)]}
`;
const PART = (file: string, connectors: Record<string, unknown> = {}) => ({ role: "fixture", file, material: "PLA", connectors });

/** Build the fixture designs the specs share (a block with a taller second revision, and an assembly), and serve the panel UI. */
export default async function globalSetup() {
  for (const built of ["vscode-extension/out/ui/panel.js", "vscode-extension/out/designUi.js"]) {
    if (!fs.existsSync(path.join(repo, built))) throw new Error(`${built} is missing: run \`npm --prefix vscode-extension run compile\` first`);
  }

  const folder = path.join(WORK, "project", "designs", "block");
  const design = await api<Design>("POST", "/api/designs", { path: folder, create: { name: "Block", backend: "build123d" } });
  writeDesign(folder, { parameters: PARAMETERS, ground: "block", parts: { block: PART("parts/block.py") } },
    { "parts/block.py": BLOCK, "assembly.py": PLACE_ONE("block") });
  await api("POST", `/api/designs/${design.id}/build`);
  await api("POST", `/api/designs/${design.id}/parameters`, { values: { height: 20 }, expected: { height: 10 } });

  const assemblyFolder = path.join(WORK, "project", "designs", "pair");
  const assembly = await api<Design>("POST", "/api/designs", { path: assemblyFolder, create: { name: "Pair", backend: "build123d" } });
  writeDesign(assemblyFolder, {
    ground: "base",
    parts: { base: PART("parts/pair.py", { top: { kind: "face" } }), lid: PART("parts/pair.py", { bottom: { kind: "face" } }) },
    mates: [{ type: "rigid", a: "base.top", b: "lid.bottom", offset: 0.5 }],
  }, { "parts/pair.py": PAIR, "assembly.py": PAIR_ASSEMBLY });
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
