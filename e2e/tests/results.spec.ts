import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { api, openPanel, WORK } from "../helpers";

interface Revision {
  report: { tally: { text: string } };
}

test("results: problems by cause, pattern copies folded, counted as the agent reads them", async ({ page }) => {
  // M5 inserts for the M3 screws: each insert sits off its mate and into the lid, and no screw takes its thread.
  const folder = path.join(WORK, "project", "designs", "enclosure-m5");
  fs.cpSync(path.resolve(__dirname, "../../backend/tests/designs/enclosure"), folder, { recursive: true });
  const interfaces = path.join(folder, "interfaces.py");
  fs.writeFileSync(interfaces, fs.readFileSync(interfaces, "utf8").replace('"M3-0.5-Standard"', '"M5-0.8-Standard"'));
  const { id } = await api<{ id: string }>("POST", "/api/designs", { path: folder });
  const revision = await api<Revision>("POST", `/api/designs/${id}/build`);

  await openPanel(page, { name: "inspector", id });
  await page.locator(".tab", { hasText: "Results" }).click();
  const problems = page.locator(".problems > li");
  await expect(problems).toHaveCount(3);
  await expect(problems.nth(0)).toContainText("Mates base.inserts+insert.placement ×4 at [0, 1, 2, 3]");
  await expect(problems.nth(0).locator(".also")).toHaveText("also: Interference lid+insert[0] ×4");
  await expect(problems.nth(1)).toContainText("Fastener sets screw.seat:lid.screws>insert.thread ×4 at [0, 1, 2, 3]");
  await expect(problems.nth(2).locator(".check-mark")).toHaveText("!"); // a warning: the thinner walls round M5 holes
  await problems.nth(0).locator(".problem-checks summary").click();
  await expect(problems.nth(0).locator(".problem-checks li")).toHaveCount(8);

  // The same count, in the same words, as the agent reads it
  const tally = "2 problems (12 failing checks), 1 warning; 24 checks pass";
  expect(revision.report.tally.text).toBe(tally);
  await expect(page.locator(".report .tally")).toHaveText(tally);
  const { overview } = await api<{ overview: string }>("GET", `/api/designs/${id}/structure`);
  expect(overview).toContain(tally);
  await expect(page.locator(".tab", { hasText: "Results" }).locator(".tab-count")).toHaveText("3");
});
