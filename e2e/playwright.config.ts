import { defineConfig } from "@playwright/test";
import { BASE_URL, HARNESS_URL, PORT } from "./helpers";

// One real `hitlcad serve` on its own port and data folder, and the built panel UI as webviews load it (harness.ts).
export default defineConfig({
  testDir: "tests",
  globalSetup: "./global-setup.ts",
  workers: 1, // the specs share one server and its fixture designs
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    browserName: "chromium",
    baseURL: HARNESS_URL,
    viewport: { width: 1100, height: 700 },
    trace: "retain-on-failure",
    // WebGL2 without a GPU: three-cad-viewer's GPU picking needs it.
    launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] },
  },
  webServer: {
    command: `rm -rf .work && mkdir -p .work && HITLCAD_DATA_DIR=.work/data HITLCAD_PORT=${PORT} HITLCAD_CORS_ORIGINS='["${HARNESS_URL}"]' uv run --project .. hitlcad serve`,
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
