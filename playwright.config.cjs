const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/ui",
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8788",
    browserName: "chromium",
    launchOptions: {
      ...(process.env.MUSE_TEST_BROWSER
        ? { executablePath: process.env.MUSE_TEST_BROWSER }
        : {}),
    },
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      process.platform === "win32"
        ? ".venv\\Scripts\\python.exe -m tests.ui_server"
        : ".venv/bin/python -m tests.ui_server",
    url: "http://127.0.0.1:8788",
    reuseExistingServer: false,
    timeout: 20000,
  },
});
