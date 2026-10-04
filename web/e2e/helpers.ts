/** Shared steps and API helpers of the end-to-end tests. */
import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import type { ConversationDetail, Run, RunEvent, Settings } from "../src/api/types";
import { FAKE_LLM_URL } from "./env";

/** A short random tag that makes messages (and conversation titles) unique per test. */
export function uniqueTag(): string {
  return `#${Math.random().toString(36).slice(2, 8)}`;
}

/** Opens the new-chat page and waits for the composer. */
export async function openNewChat(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("composer-input")).toBeVisible();
}

/** Types a message and sends it with the send button (works on touch and desktop). */
export async function sendMessage(page: Page, text: string): Promise<void> {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-send").click();
  await expect(page.getByTestId("message-user").filter({ hasText: text })).toBeVisible();
}

/** Id of the conversation shown on the page (waits for the /c/:id URL). */
export async function currentConversationId(page: Page): Promise<string> {
  await expect(page).toHaveURL(/\/c\/[^/]+$/);
  return new URL(page.url()).pathname.split("/").pop() as string;
}

/** The title in the page header (the conversation title on chat pages). */
export function pageTitle(page: Page): Locator {
  return page.locator("header").getByRole("heading", { level: 1 });
}

/** The activity card of the latest run. */
export function latestActivity(page: Page): Locator {
  return page.getByTestId("activity").last();
}

/** The latest assistant answer. */
export function latestAnswer(page: Page): Locator {
  return page.getByTestId("message-assistant").last();
}

/** Waits for the latest run to reach `status` (the activity card mirrors the run status). */
export async function expectRunStatus(page: Page, status: Run["status"], timeout = 60_000): Promise<void> {
  await expect(latestActivity(page)).toHaveAttribute("data-status", status, { timeout });
}

/** Expands the (collapsed) activity card of a finished run. */
export async function expandActivity(activity: Locator): Promise<void> {
  await activity.getByRole("button", { name: "Show activity" }).click();
  await expect(activity.getByRole("button", { name: "Hide activity" })).toBeVisible();
}

/** Opens the files panel (docked on wide screens, a full-screen sheet on phones). */
export async function openFiles(page: Page): Promise<Locator> {
  await page.getByTestId("files-button").click();
  const panel = page.getByTestId("files-panel");
  await expect(panel).toBeVisible();
  return panel;
}

/** Shows the sidebar: docked on desktop, the off-canvas drawer on phones. */
export async function openSidebar(page: Page, isMobile: boolean): Promise<Locator> {
  const sidebar = page.getByTestId("sidebar");
  if (isMobile) await page.getByTestId("sidebar-toggle").click();
  await expect(sidebar).toBeVisible();
  return sidebar;
}

/** Whether the document is wider than the viewport (horizontal scrolling). */
export async function horizontalOverflow(page: Page): Promise<{ scrollWidth: number; innerWidth: number }> {
  return page.evaluate(() => ({
    scrollWidth: (document.scrollingElement ?? document.documentElement).scrollWidth,
    innerWidth: window.innerWidth,
  }));
}

export async function expectNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const { scrollWidth, innerWidth } = await horizontalOverflow(page);
  expect(scrollWidth, `${label}: page wider than the ${innerWidth}px viewport`).toBeLessThanOrEqual(innerWidth);
}

// ------------------------------------------------------------------ API

async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const response = await request.get(url);
  expect(response.status(), `GET ${url}`).toBe(200);
  return (await response.json()) as T;
}

export function getConversation(request: APIRequestContext, id: string): Promise<ConversationDetail> {
  return getJson<ConversationDetail>(request, `/api/conversations/${id}`);
}

export async function getLatestRun(request: APIRequestContext, conversationId: string): Promise<Run> {
  const detail = await getConversation(request, conversationId);
  const run = detail.runs.at(-1);
  expect(run, "the conversation has a run").toBeTruthy();
  return run as Run;
}

export function getRunEvents(request: APIRequestContext, runId: string): Promise<RunEvent[]> {
  return getJson<RunEvent[]>(request, `/api/runs/${runId}/events.json`);
}

export function getSettings(request: APIRequestContext): Promise<Settings> {
  return getJson<Settings>(request, "/api/settings");
}

/** Text of a workspace file of a conversation. */
export async function getFileText(request: APIRequestContext, conversationId: string, path: string): Promise<string> {
  const response = await request.get(`/api/conversations/${conversationId}/files/${path}`);
  expect(response.status(), `file ${path}`).toBe(200);
  return response.text();
}

export interface FakeLlmRequest {
  kind: string;
  model: string | null;
  stream: boolean;
  tools: string[];
  last_user: string;
}

/** Requests the fake LLM received whose latest user message contains `marker`. */
export async function fakeLlmRequests(request: APIRequestContext, marker: string): Promise<FakeLlmRequest[]> {
  expect(FAKE_LLM_URL, "E2E_FAKE_LLM_URL must be set").toBeTruthy();
  const all = await getJson<FakeLlmRequest[]>(request, `${FAKE_LLM_URL}/fake/requests`);
  return all.filter((entry) => entry.last_user.includes(marker));
}
