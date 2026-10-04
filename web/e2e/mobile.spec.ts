import { expect, test, type Page } from "@playwright/test";

import {
  currentConversationId,
  expectNoHorizontalOverflow,
  expectRunStatus,
  openFiles,
  openNewChat,
  pageTitle,
  sendMessage,
  uniqueTag,
} from "./helpers";

async function expectTouchTarget(page: Page, testId: string): Promise<void> {
  const box = await page.getByTestId(testId).boundingBox();
  expect(box, `${testId} is laid out`).not.toBeNull();
  expect(Math.min(box!.width, box!.height), `${testId} touch target`).toBeGreaterThanOrEqual(44);
}

test("the drawer navigates between chats, a new chat and settings", async ({ page }) => {
  const title = `Drawer target ${uniqueTag()}`;
  const created = await page.request.post("/api/conversations", { data: { title } });
  expect(created.status()).toBe(200);
  const { id } = (await created.json()) as { id: string };

  await page.goto("/");
  const sidebar = page.getByTestId("sidebar");
  await expect(sidebar).toBeHidden();
  await expectTouchTarget(page, "sidebar-toggle");

  await page.getByTestId("sidebar-toggle").click();
  await expect(sidebar).toBeVisible();
  await sidebar.getByTestId("conversation-item").filter({ hasText: title }).click();
  await expect(page).toHaveURL(new RegExp(`/c/${id}$`));
  await expect(sidebar).toBeHidden();
  await expect(pageTitle(page)).toHaveText(title);

  // The overlay closes the drawer without navigating.
  await page.getByTestId("sidebar-toggle").click();
  await expect(sidebar).toBeVisible();
  await page.touchscreen.tap(380, 400);
  await expect(sidebar).toBeHidden();
  await expect(page).toHaveURL(new RegExp(`/c/${id}$`));

  await page.getByTestId("sidebar-toggle").click();
  await sidebar.getByTestId("new-chat").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(sidebar).toBeHidden();

  await page.getByTestId("sidebar-toggle").click();
  await sidebar.getByTestId("settings-link").click();
  await expect(page).toHaveURL(/\/settings\/account$/);
  await expect(sidebar).toBeHidden();

  await page.request.delete(`/api/conversations/${id}`);
});

test("the composer works with touch input", async ({ page }) => {
  await openNewChat(page);
  const input = page.getByTestId("composer-input");
  expect(parseFloat(await input.evaluate((element) => getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(16);
  await expectTouchTarget(page, "composer-send");

  // On touch devices Enter inserts a new line; the send button sends.
  const tag = uniqueTag();
  await input.tap();
  await input.pressSequentially("Hi there");
  await input.press("Enter");
  await input.pressSequentially(tag);
  await expect(input).toHaveValue(`Hi there\n${tag}`);
  await expect(page).toHaveURL(/\/$/);

  await page.getByTestId("composer-send").tap();
  await expect(page.getByTestId("message-user")).toHaveText(`Hi there\n${tag}`);
  await currentConversationId(page);
  await expect(input).toHaveValue("");
  await expectRunStatus(page, "completed");
  await expect(page.getByTestId("message-assistant").last()).toContainText("Hello! 👋");
});

test("no horizontal scrolling at phone widths", async ({ page }) => {
  await openNewChat(page);
  await page.getByTestId("mode-picker").tap();
  await page.getByTestId("mode-option-team").tap();
  await sendMessage(page, `Team: a file with a long table ${uniqueTag()}`);
  await expectRunStatus(page, "completed");

  for (const width of [320, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    await expectNoHorizontalOverflow(page, `chat with a team answer at ${width}px`);
    const panel = await openFiles(page);
    await panel.getByTestId("file-row").filter({ hasText: "summary.md" }).click();
    await expect(panel.getByRole("heading", { name: "Summary" })).toBeVisible();
    await expectNoHorizontalOverflow(page, `file preview at ${width}px`);
    await panel.getByRole("button", { name: "Back to files" }).tap();
    await panel.getByRole("button", { name: "Close files" }).tap();
    await expect(panel).toBeHidden();
  }

  await page.setViewportSize({ width: 320, height: 700 });
  for (const path of ["/", "/settings/account", "/settings/model", "/settings/users"]) {
    await page.goto(path);
    await expect(page.getByRole("main")).toBeVisible();
    await expectNoHorizontalOverflow(page, `${path} at 320px`);
  }
});
