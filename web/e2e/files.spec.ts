import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

import {
  currentConversationId,
  expectRunStatus,
  fakeLlmRequests,
  getConversation,
  latestAnswer,
  openFiles,
  openNewChat,
  uniqueTag,
} from "./helpers";

test("attached files reach the agent and the workspace files can be managed", async ({ page }) => {
  const tag = uniqueTag();
  await openNewChat(page);

  // Attaching a file to a new chat creates the conversation and uploads it.
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Attach files" }).click();
  await (await chooser).setFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("e2e notes\n") });
  const conversationId = await currentConversationId(page);
  const composer = page.getByRole("form", { name: "Message composer" });
  await expect(composer.getByRole("list", { name: "Attachments" })).toContainText("notes.txt");
  await expect(page.getByTestId("composer-send")).toBeEnabled();

  await page.getByTestId("composer-input").fill(`Read the attached file ${tag}`);
  await page.getByTestId("composer-send").click();
  const message = page.getByTestId("message-user").filter({ hasText: tag });
  await expect(message).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Attachments" }).getByRole("button", { name: "notes.txt" }),
  ).toBeVisible();
  await expectRunStatus(page, "completed");
  await expect(latestAnswer(page)).toContainText("hello.txt");

  const detail = await getConversation(page.request, conversationId);
  expect(detail.messages[0].attachments).toEqual(["uploads/notes.txt"]);
  const routed = (await fakeLlmRequests(page.request, tag)).filter((request) => request.kind === "route");
  expect(routed[0].last_user).toContain("- uploads/notes.txt");

  // Upload another file from the files panel, then delete the agent's file.
  const panel = await openFiles(page);
  await expect(panel.getByTestId("file-row")).toHaveCount(2);
  const upload = page.waitForEvent("filechooser");
  await panel.getByRole("button", { name: "Upload files" }).click();
  await (await upload).setFiles({ name: "data.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n") });
  await expect(page.getByText("1 file uploaded")).toBeVisible();
  await expect(panel.getByTestId("file-row")).toHaveCount(3);

  await panel.getByRole("button", { name: "Delete hello.txt" }).click();
  await page.getByRole("dialog", { name: "Delete file?" }).getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("File deleted")).toBeVisible();
  await expect(panel.getByTestId("file-row")).toHaveCount(2);
  await expect(panel.getByTestId("file-row").filter({ hasText: "hello.txt" })).toHaveCount(0);

  const [zip] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("link", { name: "Download all (.zip)" }).click(),
  ]);
  expect(zip.suggestedFilename()).toMatch(/\.zip$/);
  const archive = await readFile(await zip.path());
  expect(archive.subarray(0, 2).toString()).toBe("PK");
  expect(archive.includes("uploads/notes.txt")).toBe(true);
  expect(archive.includes("hello.txt")).toBe(false);
});

test("a conversation can be pinned and exported as Markdown", async ({ page }) => {
  const tag = uniqueTag();
  await openNewChat(page);
  await page.getByTestId("composer-input").fill(`Hello export ${tag}`);
  await page.getByTestId("composer-send").click();
  const conversationId = await currentConversationId(page);
  await expectRunStatus(page, "completed");

  const menuButton = page.locator("header").getByRole("button", { name: "Chat actions" });
  await menuButton.click();
  await page.getByRole("menuitem", { name: "Pin" }).click();
  await expect.poll(async () => (await getConversation(page.request, conversationId)).conversation.pinned).toBe(true);

  await menuButton.click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Export as Markdown" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);
  const markdown = await readFile(await download.path(), "utf8");
  expect(markdown).toContain(`Hello export ${tag}`);
  expect(markdown).toContain("What shall we work on?");

  await page.request.delete(`/api/conversations/${conversationId}`);
});
