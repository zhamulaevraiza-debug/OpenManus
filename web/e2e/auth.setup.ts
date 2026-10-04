import { expect, test as setup } from "@playwright/test";

import { ADMIN, STORAGE_STATE } from "./env";

setup("sign in as the administrator", async ({ request }) => {
  expect(ADMIN.password, "E2E_ADMIN_PASSWORD must be set (use scripts/e2e.sh)").not.toBe("");
  const response = await request.post("/api/auth/login", { data: ADMIN });
  expect(response.status()).toBe(200);
  expect(await response.json()).toMatchObject({ username: ADMIN.username, is_admin: true });
  await request.storageState({ path: STORAGE_STATE });
});
