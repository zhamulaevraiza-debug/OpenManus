import { expect, test } from "@playwright/test";

import {
  currentConversationId,
  expectRunStatus,
  openNewChat,
  pageTitle,
  openSidebar,
  sendMessage,
  uniqueTag,
} from "./helpers";

test("a conversation can be renamed and deleted", async ({ page, isMobile }) => {
  const tag = uniqueTag();
  await openNewChat(page);
  await sendMessage(page, `Hello there ${tag}`);
  const conversationId = await currentConversationId(page);
  await expectRunStatus(page, "completed");

  // Rename from the chat header menu.
  const title = `Renamed chat ${tag}`;
  await page.locator("header").getByRole("button", { name: "Chat actions" }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const renameDialog = page.getByRole("dialog", { name: "Rename chat" });
  await renameDialog.getByLabel("Title").fill(title);
  await renameDialog.getByRole("button", { name: "Save" }).click();
  await expect(renameDialog).toBeHidden();
  await expect(pageTitle(page)).toHaveText(title);

  // Delete from the sidebar (the drawer on phones).
  const sidebar = await openSidebar(page, isMobile);
  const item = sidebar.getByTestId("conversation-item").filter({ hasText: title });
  await expect(item).toBeVisible();
  await expect(item).toHaveAttribute("aria-current", "page");
  const row = sidebar
    .getByRole("listitem")
    .filter({ has: page.getByTestId("conversation-item").filter({ hasText: title }) });
  await row.getByRole("button", { name: "Chat actions" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  const deleteDialog = page.getByRole("dialog", { name: "Delete chat?" });
  await expect(deleteDialog).toContainText(title);
  await deleteDialog.getByRole("button", { name: "Delete" }).click();

  await expect(page.getByText("Chat deleted")).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  expect((await page.request.get(`/api/conversations/${conversationId}`)).status()).toBe(404);
  const list = await (await page.request.get(`/api/conversations?q=${encodeURIComponent(tag)}`)).json();
  expect(list).toEqual([]);
  if (!isMobile) await expect(sidebar.getByTestId("conversation-item").filter({ hasText: title })).toHaveCount(0);
});
