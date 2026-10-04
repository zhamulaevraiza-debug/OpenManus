import { expect, test } from "@playwright/test";

import {
  currentConversationId,
  expectRunStatus,
  getLatestRun,
  getRunEvents,
  latestAnswer,
  openNewChat,
  pageTitle,
  sendMessage,
  uniqueTag,
} from "./helpers";

declare global {
  interface Window {
    __sawStreamingAnswer?: boolean;
  }
}

test("a greeting is routed to chat and answered with a streamed reply", async ({ page }) => {
  await openNewChat(page);
  // Remember whether the answer was ever rendered while still streaming.
  await page.evaluate(() => {
    window.__sawStreamingAnswer = false;
    new MutationObserver(() => {
      if (document.querySelector('[data-testid="message-assistant"][data-streaming]')) {
        window.__sawStreamingAnswer = true;
      }
    }).observe(document.body, { subtree: true, childList: true, attributes: true });
  });

  const text = `Привет! Как дела? ${uniqueTag()}`;
  await sendMessage(page, text);
  const conversationId = await currentConversationId(page);

  await expect(latestAnswer(page)).toContainText("Привет! 👋 Я OpenManus — команда ИИ-агентов.");
  await expect(latestAnswer(page)).toContainText("Чем займёмся?");
  await expectRunStatus(page, "completed");
  await expect(latestAnswer(page)).not.toHaveAttribute("data-streaming");
  expect(await page.evaluate(() => window.__sawStreamingAnswer)).toBe(true);

  // The first message titles the conversation.
  await expect(pageTitle(page)).toHaveText(text);

  const run = await getLatestRun(page.request, conversationId);
  expect(run).toMatchObject({ mode: "auto", status: "completed", error: null });
  expect(run.usage?.input_tokens).toBeGreaterThan(0);
  const events = await getRunEvents(page.request, run.id);
  expect(events.find((event) => event.type === "router.decision")?.data).toMatchObject({ mode: "chat" });
  expect(events.filter((event) => event.type === "answer.delta").length).toBeGreaterThan(3);
  expect(events.at(-1)?.type).toBe("run.finished");
});
