const { test, expect } = require("@playwright/test");

async function fillProfile(page) {
  await page.locator("#first-name").fill("Haru");
  await page.locator("#last-name").fill("Mori");
  await page.locator("#birthday").fill("1993-11-14");
  await page.locator("#consent").check();
}

test("manual OTP, persistent input, request log and credential download", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#connection-text")).toHaveText("本地服务已连接");
  await page.locator("#email").fill("manual-ui@example.test");
  await fillProfile(page);
  await page.locator("#submit-register").click();
  await expect(page.locator("#otp-panel")).toBeVisible();
  await page.locator("#manual-otp").fill("123");
  await expect
    .poll(async () => Number(await page.locator("#count-active").textContent()))
    .toBe(1);
  await page.waitForTimeout(2000);
  await expect(page.locator("#manual-otp")).toHaveValue("123");
  await page.locator("#manual-otp").fill("123456");
  await page.locator("#submit-otp").click();
  await expect(page.locator("#progress-badge")).toHaveText("会话已验证");
  await expect(page.locator("#event-list")).toContainText(
    "register-completion",
  );
  await expect(page.locator("#event-list")).not.toContainText("fixture-secret");
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#export-task").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^muse-account-.+\.json$/);
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const result = JSON.parse(Buffer.concat(chunks).toString());
  expect(result.hatch_access_verified).toBe(true);
  expect(result.hatch_sess).toBe("fixture-secret-cookie");
  expect(result.protocol_fingerprint.browser).toBe("Chrome 152");
  await expect(page.locator("#fingerprint-summary")).toContainText(
    result.protocol_fingerprint.id.slice(0, 12),
  );
  expect(errors).toEqual([]);
});

test("batch import and filtering", async ({ page }) => {
  await page.goto("/");
  await page.locator("#tab-batch").click();
  await page
    .locator("#batch-accounts")
    .fill(
      "batch-1@example.test----https://mail.example.test/one\nbatch-2@example.test----https://mail.example.test/two",
    );
  await fillProfile(page);
  await page.locator("#submit-register").click();
  await expect(page.locator("#task-rows")).toContainText(
    "batch-1@example.test",
  );
  await expect(page.locator("#task-rows")).toContainText(
    "batch-2@example.test",
  );
  await expect
    .poll(async () => page.locator("#task-rows .badge.succeeded").count())
    .toBeGreaterThanOrEqual(2);
  await page.locator("#search").fill("batch-1@");
  await expect(page.locator("#task-rows tr")).toHaveCount(1);
  await page.locator("#search").fill("");
  await page.locator("#status-filter").selectOption("attention");
  await expect(page.locator("#task-rows tr")).toHaveCount(0);
  await expect(page.locator("#empty-state")).toBeVisible();
  const batchTasks = await page.evaluate(async () => {
    const response = await fetch("/api/tasks", {
      headers: { "X-Muse-Client": "1" },
    });
    return (await response.json()).tasks.filter((task) =>
      task.email.startsWith("batch-"),
    );
  });
  expect(new Set(batchTasks.map((task) => task.fingerprint.id)).size).toBe(2);
});

test("desktop and mobile layouts have no horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto("/");
  await expect(page.locator("h1")).toBeVisible();
  await page.locator(".advanced summary").click();
  await page.locator("#proxy-mode").selectOption("dynamic");
  await page.locator("#front-proxy-enabled").check();
  await page.screenshot({
    path: "artifacts/muse-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "artifacts/muse-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.locator("#guide-open").click();
  await expect(page.locator("#guide")).toBeVisible();
  await page.locator("#guide-close").click();
  await expect(page.locator("#guide")).not.toBeVisible();
});

test("dynamic and front proxy controls submit independently", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".advanced summary").click();
  await page.locator("#proxy-mode").selectOption("dynamic");
  await expect(page.locator("#dynamic-hint")).toBeVisible();
  await page
    .locator("#proxy")
    .fill("host.test:8000:user-session-{session}:private-password");
  await page.locator("#front-proxy-enabled").check();
  await page.locator("#front-proxy").fill("http://127.0.0.1:7897");
  let testPayload;
  await page.route("**/api/proxy/test", async (route) => {
    testPayload = route.request().postDataJSON();
    await route.fulfill({
      json: {
        connected: true,
        exit_ip: "203.0.113.11",
        route: "前置 → 注册代理",
        elapsed_ms: 50,
        message: "两次出口采样一致",
      },
    });
  });
  await page.locator("#test-proxy").click();
  await expect(page.locator("#proxy-result")).toContainText("203.0.113.11");
  expect(testPayload.proxy_mode).toBe("dynamic");
  expect(testPayload.front_proxy_enabled).toBe(true);
  expect(testPayload.front_proxy).toBe("http://127.0.0.1:7897");
  expect(testPayload.proxy_check).toBe(true);
  await page.locator("#email").fill("proxy-ui@example.test");
  await fillProfile(page);
  const submitted = page.waitForRequest(
    (request) =>
      request.url().endsWith("/api/tasks") && request.method() === "POST",
  );
  await page.locator("#submit-register").click();
  expect((await submitted).postDataJSON().accounts[0]).toMatchObject(
    testPayload,
  );
  await expect(page.locator("#otp-panel")).toBeVisible();
  await page.waitForTimeout(1900);
  await expect(page.locator("#front-proxy")).toHaveValue(
    "http://127.0.0.1:7897",
  );
  await page.locator("#manual-otp").fill("123456");
  await page.locator("#submit-otp").click();
  await expect(page.locator("#task-note")).toContainText("203.0.113.11");
  await expect(page.locator("#event-list")).not.toContainText(
    "private-password",
  );
  await page.locator("#front-proxy-enabled").uncheck();
  await expect(page.locator("#front-proxy")).toBeDisabled();
});

test("mailbox fingerprint persists after cancellation and rejects profile changes", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".advanced summary").click();
  await page.locator("#browser-profile").selectOption("chrome152_macos");
  await page.locator("#email").fill("fingerprint-ui@example.test");
  await fillProfile(page);
  const firstResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/tasks") &&
      response.request().method() === "POST",
  );
  await page.locator("#submit-register").click();
  const firstTask = (await (await firstResponse).json()).tasks[0];
  await expect(page.locator("#otp-panel")).toBeVisible();
  await expect(page.locator("#fingerprint-summary")).toContainText(
    "Chrome 152 / macOS",
  );
  await page.locator("#cancel-task").click();
  await expect(page.locator("#progress-badge")).toHaveText("已停止");
  await page.locator("#browser-profile").selectOption("chrome152");
  await page.locator("#submit-register").click();
  await expect(page.locator("#form-error")).toContainText("固定协议指纹");
  await page.locator("#browser-profile").selectOption("auto");
  const retryResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/tasks") &&
      response.request().method() === "POST",
  );
  await page.locator("#submit-register").click();
  const retryTask = (await (await retryResponse).json()).tasks[0];
  expect(retryTask.fingerprint).toEqual(firstTask.fingerprint);
  await expect(page.locator("#otp-panel")).toBeVisible();
  await page.locator("#cancel-task").click();
  await expect(page.locator("#progress-badge")).toHaveText("已停止");
});
