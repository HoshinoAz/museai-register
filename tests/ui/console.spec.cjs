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
});

test("desktop and mobile layouts have no horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto("/");
  await expect(page.locator("h1")).toBeVisible();
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
