import { expect, test } from "@playwright/test";

import type { User } from "../src/api/types";
import { expectRunStatus, latestAnswer, openNewChat, sendMessage, uniqueTag } from "./helpers";

test("an administrator adds a user who sees only their own chats and no admin settings", async ({ page, browser }) => {
  const tag = uniqueTag().slice(1);
  const username = `e2e-${tag}`;
  const password = `secret-${tag}-pw`;
  const created = await page.request.post("/api/conversations", { data: { title: `Admin only ${tag}` } });
  const adminConversation = ((await created.json()) as { id: string }).id;

  try {
    await page.goto("/settings/users");
    await page.getByRole("button", { name: "Add user" }).click();
    const dialog = page.getByRole("dialog", { name: "New user" });
    await dialog.getByLabel("Username").fill(username);
    await dialog.getByLabel("Password").fill(password);
    await dialog.getByRole("button", { name: "Add user" }).click();
    await expect(page.getByText("User created")).toBeVisible();
    await expect(page.getByText(username, { exact: true })).toBeVisible();

    const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const userPage = await context.newPage();
    await userPage.goto("/login");
    await userPage.getByTestId("login-username").fill(username);
    await userPage.getByTestId("login-password").fill(password);
    await userPage.getByTestId("login-submit").click();
    await expect(userPage.getByRole("heading", { name: `How can I help, ${username}?` })).toBeVisible();

    await userPage.goto("/settings/account");
    await expect(userPage.getByRole("navigation", { name: "Settings sections" }).getByRole("link")).toHaveText([
      "Account",
      "Appearance",
      "About",
    ]);
    expect((await userPage.request.get("/api/settings")).status()).toBe(403);
    expect((await userPage.request.get("/api/users")).status()).toBe(403);
    expect(await (await userPage.request.get("/api/conversations")).json()).toEqual([]);
    expect((await userPage.request.get(`/api/conversations/${adminConversation}`)).status()).toBe(404);
    await userPage.goto(`/c/${adminConversation}`);
    await expect(userPage.getByText("Chat not found")).toBeVisible();

    await openNewChat(userPage);
    await sendMessage(userPage, `Hello from a new user ${tag}`);
    await expectRunStatus(userPage, "completed");
    await expect(latestAnswer(userPage)).toContainText("Hello! 👋");
    await context.close();
  } finally {
    const users = (await (await page.request.get("/api/users")).json()) as User[];
    const user = users.find((entry) => entry.username === username);
    if (user) expect((await page.request.delete(`/api/users/${user.id}`)).status()).toBe(204);
    await page.request.delete(`/api/conversations/${adminConversation}`);
  }
});
