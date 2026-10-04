import { expect, test } from "@playwright/test";

import {
  currentConversationId,
  expandActivity,
  expectRunStatus,
  getLatestRun,
  getRunEvents,
  latestActivity,
  latestAnswer,
  openFiles,
  openNewChat,
  sendMessage,
  uniqueTag,
} from "./helpers";

const STEPS = ["Create hello.txt with a friendly greeting", "Write summary.md describing hello.txt"];

test("team mode plans two steps for two agents and completes both", async ({ page }) => {
  await openNewChat(page);
  await page.getByTestId("mode-picker").click();
  await page.getByTestId("mode-option-team").click();
  await expect(page.getByTestId("mode-picker")).toHaveAccessibleName("Mode: Team");

  await sendMessage(page, `Prepare a greeting file and a short summary ${uniqueTag()}`);
  const conversationId = await currentConversationId(page);

  // The plan is shown live while the team works.
  const activity = latestActivity(page);
  await expect(activity.getByTestId("plan")).toBeVisible();
  await expectRunStatus(page, "completed");

  await expandActivity(activity);
  const plan = activity.getByTestId("plan");
  await expect(plan).toContainText("Greeting file and summary");
  await expect(plan).toContainText("2 of 2");
  const steps = plan.getByRole("listitem");
  await expect(steps).toHaveCount(2);
  await expect(steps.nth(0)).toContainText(STEPS[0]);
  await expect(steps.nth(0)).toContainText("Coder");
  await expect(steps.nth(1)).toContainText(STEPS[1]);
  await expect(steps.nth(1)).toContainText("Writer");

  const answer = latestAnswer(page);
  await expect(answer.getByRole("row")).toHaveCount(3);
  await expect(answer.getByRole("row").nth(1)).toContainText("✅ completed");
  await expect(answer.getByRole("row").nth(2)).toContainText("✅ completed");

  const run = await getLatestRun(page.request, conversationId);
  expect(run.mode).toBe("team");
  const events = await getRunEvents(page.request, run.id);
  const finished = events.filter((event) => event.type === "plan.step_finished");
  expect(finished.map((event) => [event.data.agent, event.data.status])).toEqual([
    ["coder", "completed"],
    ["writer", "completed"],
  ]);

  // Both agents worked in the shared workspace; Markdown files are previewed rendered.
  const panel = await openFiles(page);
  await expect(panel.getByTestId("file-row")).toHaveCount(2);
  await panel.getByTestId("file-row").filter({ hasText: "summary.md" }).click();
  await expect(panel.getByRole("heading", { name: "Summary" })).toBeVisible();
});
