/**
 * Documentation screenshots of the running product: desktop and phone, light and dark theme, in
 * English (`<screen>-<device>-<theme>.png`) and Russian (`…-ru.png`, a separate Russian-speaking
 * administrator). Run with `scripts/e2e.sh --screenshots`; images go to `docs/screenshots/`.
 */
import { writeFile } from "node:fs/promises";
import path from "node:path";

import { devices, expect, test, type APIRequestContext, type BrowserContextOptions, type Page } from "@playwright/test";
import sharp from "sharp";

import type { Run, SendMessageResponse } from "../src/api/types";
import { ADMIN, BASE_URL, OUTPUT_DIR, SCREENSHOTS_DIR, STORAGE_STATE } from "./env";
import { latestActivity, latestAnswer, openFiles } from "./helpers";

const MAX_BYTES = 300 * 1024;

const DEVICES: Record<"desktop" | "mobile", BrowserContextOptions> = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
  mobile: {
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    userAgent: devices["iPhone 13"].userAgent,
  },
};
const THEMES = ["light", "dark"] as const;

interface Language {
  locale: string;
  suffix: string;
  username: string;
  storageState: string;
  /** A chat, an agent task and the team task (the screenshots show the team run). */
  prompts: [string, string, string];
  done: string;
  progress: string;
  showActivity: string;
  summaryHeading: string;
  modelLabel: string;
}

const LANGUAGES: Language[] = [
  {
    locale: "en-US",
    suffix: "",
    username: ADMIN.username,
    storageState: STORAGE_STATE,
    prompts: [
      "Hello! What can you do?",
      "Create the file hello.txt with a greeting",
      "Prepare a greeting file and a short summary of it",
    ],
    done: "Done!",
    progress: "2 of 2",
    showActivity: "Show activity",
    summaryHeading: "Summary",
    modelLabel: "Model",
  },
  {
    locale: "ru-RU",
    suffix: "-ru",
    username: "maria",
    storageState: path.join(OUTPUT_DIR, "auth", "maria.json"),
    prompts: [
      "Привет! Что ты умеешь?",
      "Создай файл hello.txt с приветствием",
      "Подготовь файл приветствия и краткую сводку",
    ],
    done: "Готово!",
    progress: "2 из 2",
    showActivity: "Показать ход работы",
    summaryHeading: "Сводка",
    modelLabel: "Модель",
  },
];

test.describe.configure({ mode: "serial" });

/** Team conversation id per language (locale). */
const teamConversations = new Map<string, string>();

async function waitForRun(request: APIRequestContext, run: Run): Promise<void> {
  await expect
    .poll(async () => ((await (await request.get(`/api/runs/${run.id}`)).json()) as Run).status, {
      timeout: 60_000,
    })
    .toBe("completed");
}

async function startConversation(request: APIRequestContext, content: string, mode: string): Promise<string> {
  const created = await request.post("/api/conversations", { data: { mode } });
  expect(created.status()).toBe(200);
  const { id } = (await created.json()) as { id: string };
  const sent = await request.post(`/api/conversations/${id}/messages`, { data: { content, mode } });
  expect(sent.status()).toBe(200);
  await waitForRun(request, ((await sent.json()) as SendMessageResponse).run);
  return id;
}

/** Saves a viewport screenshot, re-encoded (palette PNG if needed) to stay below MAX_BYTES. */
async function capture(page: Page, name: string): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  const raw = await page.screenshot({ animations: "disabled", caret: "hide" });
  let image = await sharp(raw).png({ compressionLevel: 9, effort: 10 }).toBuffer();
  if (image.length > MAX_BYTES) {
    image = await sharp(raw).png({ palette: true, quality: 95, compressionLevel: 9, effort: 10 }).toBuffer();
  }
  expect(image.length, `${name} size`).toBeLessThan(MAX_BYTES);
  await writeFile(path.join(SCREENSHOTS_DIR as string, `${name}.png`), image);
}

test.beforeAll(async ({ playwright }) => {
  const admin = await playwright.request.newContext({ baseURL: BASE_URL, storageState: STORAGE_STATE });
  const russian = LANGUAGES[1];
  const password = `${russian.username}-${Date.now()}-pw`;
  const created = await admin.post("/api/users", {
    data: { username: russian.username, password, is_admin: true },
  });
  expect(created.status()).toBe(200);
  const session = await playwright.request.newContext({ baseURL: BASE_URL });
  expect((await session.post("/api/auth/login", { data: { username: russian.username, password } })).status()).toBe(
    200,
  );
  await session.storageState({ path: russian.storageState });
  await session.dispose();

  for (const language of LANGUAGES) {
    const request = await playwright.request.newContext({ baseURL: BASE_URL, storageState: language.storageState });
    const [chat, agent, team] = language.prompts;
    await startConversation(request, chat, "auto");
    await startConversation(request, agent, "auto");
    teamConversations.set(language.locale, await startConversation(request, team, "team"));
    await request.dispose();
  }
  await admin.dispose();
});

for (const language of LANGUAGES) {
  for (const [device, options] of Object.entries(DEVICES)) {
    for (const theme of THEMES) {
      test(`${language.locale} ${device} ${theme}`, async ({ browser }) => {
        const contextOptions = { ...options, baseURL: BASE_URL, colorScheme: theme, locale: language.locale };
        const context = await browser.newContext({ ...contextOptions, storageState: language.storageState });
        const page = await context.newPage();
        const suffix = `${device}-${theme}${language.suffix}`;

        // Chat with the completed team run: plan with both steps done, the answer below.
        await page.goto(`/c/${teamConversations.get(language.locale)}`);
        await expect(latestAnswer(page)).toContainText(language.done);
        const activity = latestActivity(page);
        await activity.getByRole("button", { name: language.showActivity }).click();
        const plan = activity.getByTestId("plan");
        await expect(plan).toContainText(language.progress);
        if (device === "desktop") await plan.locator("button[aria-expanded]").first().click();
        await page.getByRole("log").evaluate((element) => element.scrollTo({ top: 0 }));
        await capture(page, `chat-${suffix}`);

        // Files panel with a rendered Markdown preview.
        await page.reload();
        await expect(latestAnswer(page)).toContainText(language.done);
        const panel = await openFiles(page);
        await panel.getByTestId("file-row").filter({ hasText: "summary.md" }).click();
        await expect(panel.getByRole("heading", { name: language.summaryHeading })).toBeVisible();
        await capture(page, `files-${suffix}`);

        // Model settings (the API key is never shown, only a hint).
        await page.goto("/settings/model");
        await expect(page.getByLabel(language.modelLabel, { exact: true })).toHaveValue(/\S/);
        await capture(page, `settings-${suffix}`);
        await context.close();

        // Sign-in page (signed out: no stored session).
        const anonymous = await browser.newContext({ ...contextOptions, storageState: { cookies: [], origins: [] } });
        const login = await anonymous.newPage();
        await login.goto("/login");
        await login.getByTestId("login-username").fill(language.username);
        await capture(login, `login-${suffix}`);
        await anonymous.close();
      });
    }
  }
}
