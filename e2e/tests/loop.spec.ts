import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, clickModel, openPanel, viewerReady, WORK } from "../helpers";

// A four-bar linkage: a loop of mates, checked at the positions it was solved at, which the human watches to pass it.
const folder = path.join(WORK, "project", "designs", "linkage");
let id = "";

test.beforeAll(async () => {
  fs.cpSync(path.resolve(__dirname, "../../backend/tests/designs/linkage"), folder, { recursive: true });
  ({ id } = await api<{ id: string }>("POST", "/api/designs", { path: folder }));
  await api("POST", `/api/designs/${id}/build`);
});

test("a loop moves through its solved positions, and passes once the human has watched it", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);

  const control = page.locator(".motion-control");
  await expect(control).toContainText("base.a ↔ crank.a");
  await expect(control).toContainText("not between them");
  const canvas = page.locator(".cad-stage canvas").first();
  const built = await canvas.screenshot();
  await control.getByLabel("Move along the mate").fill("180"); // on the slider's steps: -90 + 150 × 1.8
  await page.waitForTimeout(300);
  expect((await canvas.screenshot()).equals(built)).toBe(false); // the crank turned, the coupler and rocker followed

  await control.getByRole("button", { name: "Watched it" }).click();
  await expect(control).toContainText("watched (rev 1)", { timeout: 60_000 }); // the review's build: rev 2
  const state = await api<{ revisions: { origin: string }[] }>("GET", `/api/designs/${id}`);
  expect(state.revisions.map((r) => r.origin)).toEqual(["file", "review"]);
});

test("a pin placed with the loop moved says where the loop was", async ({ page }) => {
  await openPanel(page, { name: "design", id });
  await viewerReady(page);
  const control = page.locator(".motion-control");
  await control.getByLabel("Move along the mate").fill("180");
  await page.locator(".pin-toggle").click();
  type Pins = { pins: { instance: string | null; motion: { pair: string; kind: string; at: number } | null }[] };
  const pins = async () => (await api<Pins>("GET", `/api/designs/${id}`)).pins;
  for (const [fx, fy] of [[0.5, 0.5], [0.45, 0.5], [0.55, 0.5], [0.5, 0.45], [0.5, 0.55], [0.4, 0.5], [0.6, 0.5]]) {
    await clickModel(page, fx!, fy!);
    if ((await pins()).length) break;
  }
  const [pin] = await pins();
  expect(pin?.motion).toEqual({ pair: "base.a ↔ crank.a", kind: "angle", at: 180 });
  expect(["base", "crank", "coupler", "rocker"]).toContain(pin?.instance);
});
