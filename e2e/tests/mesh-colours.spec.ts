import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, clickModel, openPanel, viewerReady, WORK, writeDesign } from "../helpers";

// A Blender part in two colours (a red block with a white top) and a blue ball placed twice.
const TOY = `import bpy


def colour(name, rgb):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1)
    return material


def build(p, i):
    bpy.ops.mesh.primitive_cube_add(size=20, location=(0, 0, 10))
    block = bpy.context.active_object
    block.name = "block"
    block.data.materials.append(colour("red", (1, 0, 0)))
    block.data.materials.append(colour("white", (1, 1, 1)))
    max(block.data.polygons, key=lambda f: f.center.z).material_index = 1
    bpy.ops.mesh.primitive_uv_sphere_add(radius=3, location=(0, 0, 3))
    ball = bpy.context.active_object
    ball.name = "ball"
    ball.data.materials.append(colour("blue", (0, 0, 1)))
    return {"base": (block, {}), "knob": (ball, {})}
`;
const TOY_ASSEMBLY = `from mathutils import Matrix


def build(p, i):
    return {"base": [Matrix.Identity(4)], "knob": [Matrix.Translation((-5, 0, 20)), Matrix.Translation((5, 0, 20))]}
`;

test("a Blender part in several colours shows a shape per colour, and a click on one picks its instance", async ({ page }) => {
  const folder = path.join(WORK, "project", "designs", "toy");
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder, create: { name: "Toy", backend: "blender" } });
  const part = (role: string) => ({ role, file: "parts/toy.py", material: "PLA", connectors: {} });
  writeDesign(folder, { ground: "base", parts: { base: part("red block, white top"), knob: part("blue ball") } },
    { "parts/toy.py": TOY, "assembly.py": TOY_ASSEMBLY });
  await api("POST", `/api/designs/${id}/build`);

  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const tree = page.locator(".tcv_cad_tree");
  await page.locator(".tcv_expand").click(); // the whole tree: the base, in two colours, is a group of a shape per colour
  for (const name of ["base", "red", "white", "knob[0]", "knob[1]"]) await expect(tree).toContainText(name);

  await clickModel(page, 0.5, 0.6); // the block, below the balls
  await expect(page.locator(".selection-box li").first()).toContainText("Point on base at");
});
