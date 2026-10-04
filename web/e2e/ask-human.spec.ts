import { expect, test } from "@playwright/test";

import {
  currentConversationId,
  expandActivity,
  expectRunStatus,
  getFileText,
  latestActivity,
  latestAnswer,
  openFiles,
  openNewChat,
  sendMessage,
  uniqueTag,
} from "./helpers";

test("the agent asks a question, the answer given in the UI lets the run continue", async ({ page }) => {
  const tag = uniqueTag();
  await openNewChat(page);
  await sendMessage(page, `Ask me what to write into hello.txt ${tag}`);
  const conversationId = await currentConversationId(page);

  const question = page.getByRole("form", { name: "The agent needs your input" });
  await expect(question).toContainText("What should I write into hello.txt?");
  await expectRunStatus(page, "waiting_input");

  // The pending question is still there after reopening the conversation.
  await page.reload();
  await expect(question).toBeVisible();
  await expect(question).toContainText("What should I write into hello.txt?");

  const reply = `Greetings from the e2e test ${tag}`;
  await page.getByTestId("ask-human-input").fill(reply);
  await page.getByTestId("ask-human-send").click();
  await expect(question).toBeHidden();

  await expectRunStatus(page, "completed");
  await expect(latestAnswer(page)).toContainText("hello.txt");
  const activity = latestActivity(page);
  await expandActivity(activity);
  await expect(activity).toContainText("Question: What should I write into hello.txt?");
  await expect(activity).toContainText(`Your answer: ${reply}`);

  expect(await getFileText(page.request, conversationId, "hello.txt")).toBe(`${reply}\n`);
  const panel = await openFiles(page);
  await panel.getByTestId("file-row").filter({ hasText: "hello.txt" }).click();
  await expect(panel.getByText(reply)).toBeVisible();
});
