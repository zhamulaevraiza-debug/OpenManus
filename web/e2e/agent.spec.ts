import { readFile } from "node:fs/promises";

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

test("an agent run shows its tool calls and answer, and its file can be previewed and downloaded", async ({ page }) => {
  await openNewChat(page);
  await sendMessage(page, `Create the file hello.txt with a greeting ${uniqueTag()}`);
  const conversationId = await currentConversationId(page);

  await expectRunStatus(page, "completed");
  const answer = latestAnswer(page);
  await expect(answer).toContainText("Done! Your request is complete.");
  await expect(answer.getByRole("listitem")).toHaveText(["hello.txt"]);

  // The activity collapses after the run; expanded it shows the route and the tool calls.
  const activity = latestActivity(page);
  await expandActivity(activity);
  await expect(activity).toContainText("The task needs tools to work with files and code");
  await expect(activity).toContainText("I will create `hello.txt` in the workspace with Python.");
  const calls = activity.getByTestId("tool-call");
  await expect(calls).toHaveCount(2);
  await expect(calls.nth(0)).toContainText("Python");
  await expect(calls.nth(0)).toHaveAttribute("data-status", "success");
  await expect(calls.nth(1)).toContainText("Finish");
  await calls.nth(0).locator("button[aria-expanded]").click();
  await expect(calls.nth(0)).toContainText("write_text('Hello from OpenManus!\\n'");
  await expect(calls.nth(0)).toContainText("Wrote hello.txt");

  const run = await getLatestRun(page.request, conversationId);
  const types = (await getRunEvents(page.request, run.id)).map((event) => event.type);
  for (const type of ["router.decision", "agent.started", "tool.call", "tool.result", "agent.finished", "final"]) {
    expect(types).toContain(type);
  }
  expect(types).toContain("workspace.changed");

  // The file shows up in the files panel and can be previewed and downloaded.
  const panel = await openFiles(page);
  await panel.getByTestId("file-row").filter({ hasText: "hello.txt" }).click();
  await expect(panel.getByText("Hello from OpenManus!")).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("link", { name: "Download", exact: true }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("hello.txt");
  expect(await readFile(await download.path(), "utf8")).toBe("Hello from OpenManus!\n");
});
