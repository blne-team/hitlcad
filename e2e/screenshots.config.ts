import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// The README's screenshots (docs/images/): the examples/ designs on the same server and harness as the tests.
//   npx playwright test -c screenshots.config.ts
export default defineConfig({
  ...base,
  testDir: "screenshots",
  timeout: 600_000,
  use: { ...base.use, deviceScaleFactor: 2 },
});
