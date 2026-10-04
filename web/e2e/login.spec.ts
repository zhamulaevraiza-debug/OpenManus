import { expect, test } from "@playwright/test";

import { ADMIN } from "./env";
import { expectNoHorizontalOverflow } from "./helpers";

test.use({ storageState: { cookies: [], origins: [] } });

test("signing in and out", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expectNoHorizontalOverflow(page, "login page");

  await page.getByTestId("login-username").fill(ADMIN.username);
  await page.getByTestId("login-password").fill(`${ADMIN.password}-wrong`);
  await page.getByTestId("login-submit").click();
  await expect(page.getByRole("alert")).toHaveText("Incorrect username or password.");
  await expect(page).toHaveURL(/\/login$/);

  await page.getByTestId("login-password").fill(ADMIN.password);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: `How can I help, ${ADMIN.username}?` })).toBeVisible();
  await expect(page.getByTestId("composer-input")).toBeVisible();

  // The session survives a reload (httpOnly cookie).
  await page.reload();
  await expect(page.getByTestId("composer-input")).toBeVisible();

  await page.goto("/settings/account");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
});
