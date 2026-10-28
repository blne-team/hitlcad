import { expect, type Page, test } from "@playwright/test";
import { clickModel, fixtures, openPanel, sentToExtension, viewerReady } from "../helpers";

const view = (page: Page, path: string) => openPanel(page, { name: "file", path });
const items = (page: Page) => page.locator(".selection-box li");

test("STEP picks name the exact face; shift adds, Esc clears", async ({ page }) => {
  await view(page, fixtures().step);
  await viewerReady(page);
  await expect(page.locator(".model-size")).toHaveText("40.0 × 30.0 × 10.0 mm");
  await expect(page.locator(".tcv_shape_filter")).toBeVisible();

  await clickModel(page, 0.5, 0.42);
  await expect(items(page)).toHaveCount(1);
  await expect(items(page).first()).toContainText(/\((face|edge|vertex) \d+ of block\)/);

  await clickModel(page, 0.42, 0.5, { shift: true });
  await expect(items(page)).toHaveCount(2);
  expect(await sentToExtension(page)).toContain("hitlcad.selection"); // "Copy for chat" in VS Code includes it
  await page.locator(".selection-box").getByRole("button", { name: "Copy for chat" }).click();
  expect(await sentToExtension(page)).toContain("hitlcad.copy");

  await page.keyboard.press("Escape");
  await expect(items(page)).toHaveCount(0);
});

test("STL picks are points on the mesh, with no pick filter to offer", async ({ page }) => {
  await view(page, fixtures().stl);
  await viewerReady(page);
  await expect(page.locator(".tcv_shape_filter")).toBeHidden();
  await clickModel(page, 0.5, 0.42);
  await expect(items(page).first()).toContainText("Point on mesh at");
});

test("Inspect restores file selection after measuring", async ({ page }) => {
  await view(page, fixtures().step);
  await viewerReady(page);
  await page.locator(".tcv_button_distance").click();
  await page.getByRole("button", { name: "Inspect model", exact: true }).click();
  await clickModel(page, 0.5, 0.42);
  await expect(items(page)).toHaveCount(1);
});

test("the viewer and its menus fit the panel at any width", async ({ page }) => {
  for (const width of [1100, 640, 480]) {
    await page.setViewportSize({ width, height: 600 });
    await view(page, fixtures().step);
    await viewerReady(page);
    // Narrow toolbars fold button groups into "…", which open on hover.
    const grid = (await page.locator(".tcv_grid-dropdown").isVisible()) ? ".tcv_grid-dropdown" : ".tcv_ellipsis:not(.tcv_unvisible)";
    for (const menu of [null, ".tcv_shape_filter", grid]) {
      if (menu === ".tcv_shape_filter" || menu === ".tcv_grid-dropdown") await page.locator(menu).click({ timeout: 10_000 });
      if (menu?.startsWith(".tcv_ellipsis")) await page.locator(menu).last().hover({ force: true, timeout: 10_000 }); // the "…" hides itself as the group expands
      const outside = await page.evaluate(() => {
        const stage = document.querySelector(".cad-stage")!.getBoundingClientRect();
        return [...document.querySelectorAll(".cad-stage *")]
          .filter((e) => {
            const b = e.getBoundingClientRect();
            const shown = getComputedStyle(e).display !== "none" && getComputedStyle(e).visibility !== "hidden";
            return shown && b.width > 0 && (b.right > stage.right + 0.5 || b.bottom > stage.bottom + 0.5);
          })
          .map((e) => `${e.tagName}.${String(e.className).split(" ")[0]}`);
      });
      expect(outside, `${width}px, ${menu ?? "menus closed"}`).toEqual([]);
      if (menu) await page.keyboard.press("Escape");
    }
  }
});

test("an unsupported file shows the server's reason", async ({ page }) => {
  await view(page, "/etc/hostname");
  await expect(page.locator(".viewport-message.fail")).toContainText("unsupported file type");
});
