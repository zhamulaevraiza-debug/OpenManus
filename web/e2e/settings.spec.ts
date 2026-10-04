import { expect, test } from "@playwright/test";

import { LLM_API_KEY, LLM_MODEL } from "./env";
import {
  expectNoHorizontalOverflow,
  expectRunStatus,
  fakeLlmRequests,
  getSettings,
  latestAnswer,
  openNewChat,
  openSidebar,
  sendMessage,
  uniqueTag,
} from "./helpers";

const KEY_HINT = `…${LLM_API_KEY.slice(-4)}`;

test("the administrator changes the model; it persists, is used by new runs and the API key stays masked", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "settings are global: the change runs once (desktop); mobile checks the page below");
  const newModel = `${LLM_MODEL}-e2e`;
  try {
    await page.goto("/settings/model");
    const model = page.getByLabel("Model", { exact: true });
    await expect(model).toHaveValue(LLM_MODEL);
    const apiKey = page.getByLabel("API key");
    await expect(apiKey).toHaveAttribute("type", "password");
    await expect(apiKey).toHaveValue("");
    await expect(page.getByText(`A key is saved (${KEY_HINT}). Leave empty to keep it.`)).toBeVisible();
    // The base URL comes from OPENMANUS_LLM_BASE_URL and is read-only in the UI.
    await expect(page.getByLabel("Base URL")).toBeDisabled();

    await page.getByRole("button", { name: "Test connection" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible();

    await model.fill(newModel);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Settings saved")).toBeVisible();

    await page.reload();
    await expect(page.getByLabel("Model", { exact: true })).toHaveValue(newModel);
    await expect(page.getByText(`A key is saved (${KEY_HINT}). Leave empty to keep it.`)).toBeVisible();

    const raw = await (await page.request.get("/api/settings")).text();
    expect(raw).not.toContain(LLM_API_KEY);
    const settings = await getSettings(page.request);
    expect(settings.llm).toMatchObject({ model: newModel, api_key_set: true, api_key_hint: KEY_HINT });
    expect(settings.locked_by_env).toContain("llm.base_url");

    // New runs use the new model.
    const tag = uniqueTag();
    await openNewChat(page);
    await sendMessage(page, `hello model ${tag}`);
    await expectRunStatus(page, "completed");
    await expect(latestAnswer(page)).toContainText("Hello! 👋");
    const requests = await fakeLlmRequests(page.request, tag);
    expect(requests.length).toBeGreaterThan(0);
    expect(new Set(requests.map((request) => request.model))).toEqual(new Set([newModel]));
  } finally {
    const response = await page.request.put("/api/settings", { data: { llm: { model: LLM_MODEL } } });
    expect(response.status()).toBe(200);
  }
});

test("settings are reachable from the sidebar and fit the screen", async ({ page, isMobile }) => {
  await page.goto("/");
  const sidebar = await openSidebar(page, isMobile);
  await sidebar.getByTestId("settings-link").click();
  await expect(page).toHaveURL(/\/settings\/account$/);
  if (isMobile) await expect(page.getByTestId("sidebar")).toBeHidden();
  await expect(page.getByText("Signed in as")).toBeVisible();

  for (const tab of ["Appearance", "Model", "Search & Browser", "Agents & Team", "MCP servers", "Users", "About"]) {
    await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: tab }).click();
    await expect(
      page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: tab }),
    ).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page, `settings ${tab}`);
  }
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Model" }).click();
  // The desktop test may be changing the model concurrently (to `${LLM_MODEL}-e2e`).
  await expect(page.getByLabel("Model", { exact: true })).toHaveValue(new RegExp(`^${LLM_MODEL}`));
});

test("the interface language and theme are switched and remembered", async ({ page }) => {
  await page.goto("/settings/appearance");
  await page.getByRole("radiogroup", { name: "Language" }).getByRole("radio", { name: "Русский" }).click();
  await expect(page.locator("header").getByRole("heading", { name: "Настройки" })).toBeVisible();
  await page.getByRole("radiogroup", { name: "Тема" }).getByRole("radio", { name: "Тёмная" }).click();
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);

  await page.reload();
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await expect(
    page.getByRole("navigation", { name: "Разделы настроек" }).getByRole("link", { name: "Оформление" }),
  ).toBeVisible();
  await page.goto("/");
  await expect(page.getByTestId("composer-input")).toHaveAttribute("placeholder", "Напишите сообщение…");
});
