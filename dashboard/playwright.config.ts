import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

const PORT = 8790;
const venvPython =
  process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python";
export const PYTHON =
  process.env.DOUBLEBLIND_PYTHON ?? (existsSync(venvPython) ? venvPython : "python");

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } },
    },
  ],
  webServer: {
    command: `"${PYTHON}" ../scripts/dashboard_e2e.py broker --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
