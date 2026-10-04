import path from "node:path";

import { defineConfig, devices, type Project } from "@playwright/test";

import { BASE_URL, OUTPUT_DIR, SCREENSHOTS_DIR, STORAGE_STATE } from "./e2e/env";

/**
 * End-to-end tests against a running OpenManus stack. Use `scripts/e2e.sh` (or `scripts/e2e.py`),
 * which starts the fake LLM and the web server and sets the E2E_* variables read in `e2e/env.ts`.
 */
const projects: Project[] = [
  { name: "setup", testMatch: /auth\.setup\.ts/ },
  {
    name: "desktop",
    testIgnore: [/screenshots\.spec\.ts/, /mobile\.spec\.ts/],
    dependencies: ["setup"],
    use: {
      ...devices["Desktop Chrome"],
      viewport: { width: 1440, height: 900 },
      storageState: STORAGE_STATE,
    },
  },
  {
    // Runs every scenario plus the phone-only checks of mobile.spec.ts.
    name: "mobile",
    testIgnore: /screenshots\.spec\.ts/,
    dependencies: ["setup"],
    use: {
      // iPhone 13 metrics and touch input, rendered by Chromium (the browser installed for tests).
      ...devices["iPhone 13"],
      browserName: "chromium",
      viewport: { width: 390, height: 844 },
      storageState: STORAGE_STATE,
    },
  },
];

if (SCREENSHOTS_DIR) {
  projects.push({
    name: "screenshots",
    testMatch: /screenshots\.spec\.ts/,
    dependencies: ["setup"],
    use: { browserName: "chromium", storageState: STORAGE_STATE },
  });
}

export default defineConfig({
  testDir: "./e2e",
  outputDir: path.join(OUTPUT_DIR, "test-results"),
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 2,
  reporter: [["list"], ["html", { outputFolder: path.join(OUTPUT_DIR, "report"), open: "never" }]],
  use: {
    baseURL: BASE_URL,
    locale: "en-US",
    timezoneId: "UTC",
    colorScheme: "light",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects,
});
