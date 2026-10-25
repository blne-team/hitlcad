import { expect, test } from "@playwright/test";
import { api, clickModel, fixtures, openPanel, viewerReady } from "../helpers";

interface Component {
  instance: string;
  topo: string;
  index: number;
}
interface State {
  selection: { component: Component | null }[];
  pins: { id: string; resolved: boolean; component: Component | null; label: string }[];
  revisions: { id: number }[];
}

const state = () => api<State>("GET", `/api/designs/${fixtures().designId}`);

test.beforeEach(async ({ page }) => {
  await openPanel(page, { name: "design", id: fixtures().designId });
  await viewerReady(page);
});

test("a selection and a pin reach the server with their exact component", async ({ page }) => {
  await clickModel(page, 0.5, 0.42);
  await expect(page.locator(".selection-box li")).toHaveCount(1);
  const [item] = (await state()).selection;
  expect(item?.component).toMatchObject({ instance: "block", topo: expect.stringMatching(/face|edge|vertex/) });

  const before = (await state()).pins.length;
  await page.locator(".pin-toggle").click();
  await expect(page.locator(".pin-toggle")).toHaveText("Placing pins");
  await clickModel(page, 0.5, 0.42);
  await expect.poll(async () => (await state()).pins.length).toBe(before + 1);
  const pin = (await state()).pins.at(-1)!;
  expect(pin.component?.instance).toBe("block");
  expect(pin.label).toMatch(/of block\) at \(/);
});

test("compare loads the earlier revision as a ghost", async ({ page }) => {
  const revisions = (await state()).revisions.map((r) => r.id);
  const ghost = page.waitForRequest((r) => /\/revisions\/\d+\/files\/view\.json$/.test(r.url()) && !r.url().includes(`/revisions/${revisions.at(-1)}/`));
  await page.locator(".compare-toggle").click();
  await ghost;
  await expect(page.locator(".compare-toggle")).toHaveAttribute("aria-pressed", "true");
});

test("the event stream reconnects when its connection drops", async ({ page }) => {
  let attempts = 0;
  await page.routeWebSocket(/\/stream/, (ws) => (attempts++ < 2 ? ws.close() : ws.connectToServer()));
  await page.reload();
  await expect.poll(() => attempts, { timeout: 15_000 }).toBe(3); // backoff: 2 s, then 4 s
  await expect(page.locator(".conn")).toHaveText("Live");
});

test("more panels than the browser's six connections per server all stay live", async ({ context }) => {
  const pages = await Promise.all(Array.from({ length: 8 }, () => context.newPage()));
  await Promise.all(pages.map((p) => openPanel(p, { name: "design", id: fixtures().designId })));
  for (const p of pages) await expect(p.locator(".conn")).toHaveText("Live");
});

test("the panel and its 3D view follow VS Code's theme as it changes", async ({ page }) => {
  const ground = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const viewer = page.locator("body"); // three-cad-viewer marks its theme there
  expect(await ground()).toBe("rgb(230, 233, 236)");
  await expect(viewer).toHaveAttribute("data-theme", "light");
  await page.evaluate(() => document.body.setAttribute("data-vscode-theme-kind", "vscode-dark")); // as VS Code does
  expect(await ground()).toBe("rgb(17, 21, 24)");
  await expect(viewer).toHaveAttribute("data-theme", "dark");
});
