import { expect, test } from "@playwright/test";

import {
  currentConversationId,
  expectRunStatus,
  getConversation,
  getLatestRun,
  latestActivity,
  latestAnswer,
  openNewChat,
  sendMessage,
  uniqueTag,
} from "./helpers";

// "slow" makes the fake model delay every agent step by a few seconds.

test("a long run can be stopped and retried", async ({ page }) => {
  await openNewChat(page);
  await sendMessage(page, `slow: create the file hello.txt ${uniqueTag()}`);
  const conversationId = await currentConversationId(page);

  const activity = latestActivity(page);
  await expect(activity.getByTestId("tool-call").first()).toBeVisible();
  await expect(activity).toHaveAttribute("data-status", "running");
  await page.getByTestId("stop-run").click();

  await expectRunStatus(page, "cancelled");
  await expect(page.getByText("Stopped by user")).toBeVisible();
  await expect(page.getByTestId("stop-run")).toBeHidden();
  await expect(page.getByTestId("composer-send")).toBeVisible();
  const cancelled = await getLatestRun(page.request, conversationId);
  expect(cancelled.status).toBe("cancelled");

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("stop-run")).toBeVisible();
  await expectRunStatus(page, "completed");
  await expect(latestAnswer(page)).toContainText("hello.txt");
  const detail = await getConversation(page.request, conversationId);
  expect(detail.runs.map((run) => run.status)).toEqual(["cancelled", "completed"]);
});

test("reloading the page mid-run replays the activity and resumes the live stream", async ({ page }) => {
  await openNewChat(page);
  await sendMessage(page, `slow: create the file hello.txt ${uniqueTag()}`);
  await currentConversationId(page);
  await expect(latestActivity(page).getByTestId("tool-call").first()).toBeVisible();

  await page.reload();

  // Still running: the activity so far is replayed and live updates continue.
  const activity = latestActivity(page);
  await expect(activity).toHaveAttribute("data-status", "running");
  await expect(activity.getByTestId("tool-call").first()).toContainText("Python");
  await expect(page.getByTestId("stop-run")).toBeVisible();

  await expectRunStatus(page, "completed");
  await expect(latestAnswer(page)).toContainText("Done! Your request is complete.");
  await expect(page.getByTestId("composer-send")).toBeVisible();
});

test("a model provider error fails the run with a readable message and a retry", async ({ page }) => {
  await openNewChat(page);
  await sendMessage(page, `Create the file hello.txt and simulate a provider error ${uniqueTag()}`);
  await expectRunStatus(page, "failed");
  await expect(latestAnswer(page)).toContainText("Simulated provider failure");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.getByTestId("composer-send")).toBeVisible();
});
