import { expect, test } from "@playwright/test";

import { FAKE_LLM_URL } from "./env";
import {
  expandActivity,
  expectRunStatus,
  latestActivity,
  latestAnswer,
  openNewChat,
  sendMessage,
  uniqueTag,
} from "./helpers";

test("the browser agent opens a web page and its screenshot is shown in the activity", async ({ page }) => {
  test.skip(!FAKE_LLM_URL, "needs the fake LLM's test page");
  const url = `${FAKE_LLM_URL}/fake/page`;
  await openNewChat(page);
  await sendMessage(page, `Browse ${url} and take a screenshot ${uniqueTag()}`);
  await expectRunStatus(page, "completed");
  await expect(latestAnswer(page)).toContainText("Done!");

  const activity = latestActivity(page);
  await expandActivity(activity);
  await expect(activity).toContainText("The task needs a web browser");
  const call = activity.getByTestId("tool-call").filter({ hasText: "Browser" });
  await expect(call).toHaveAttribute("data-status", "success");
  await expect(call).toContainText(url);

  // The page screenshot is stored as a run artifact and shown as a thumbnail.
  const thumbnail = call.getByRole("img", { name: "Screenshot" });
  await expect(thumbnail).toBeVisible();
  expect(await thumbnail.getAttribute("src")).toMatch(/^\/api\/runs\/[^/]+\/artifacts\/[\w-]+\.jpg$/);
  await expect
    .poll(() => thumbnail.evaluate((image: HTMLImageElement) => (image.complete ? image.naturalWidth : 0)))
    .toBeGreaterThan(0);

  await call.getByRole("button", { name: "Open screenshot" }).click();
  const viewer = page.getByRole("dialog", { name: "Screenshot" });
  await expect(viewer.getByRole("img", { name: "Screenshot" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
});
