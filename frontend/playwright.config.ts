import { defineConfig } from "@playwright/test";

const configuredBaseUrl = process.env.E2E_BASE_URL || "http://127.0.0.1:8765/manufacturing-geo-audit";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  use: {
    baseURL: `${configuredBaseUrl.replace(/\/$/, "")}/`,
    channel: "chrome",
    trace: "retain-on-failure",
  },
  reporter: "list",
});
