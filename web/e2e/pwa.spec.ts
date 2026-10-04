import { expect, test } from "@playwright/test";

test("the app is installable, precaches its offline page and copes with losing the network", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("composer-input")).toBeVisible();

  const manifestUrl = await page.locator('link[rel="manifest"]').getAttribute("href");
  expect(manifestUrl).toBeTruthy();
  const manifest = await (await page.request.get(manifestUrl as string)).json();
  expect(manifest).toMatchObject({ name: "OpenManus", display: "standalone", start_url: "/", scope: "/" });
  expect(manifest.icons.map((icon: { sizes: string }) => icon.sizes)).toEqual(
    expect.arrayContaining(["192x192", "512x512"]),
  );

  // The service worker takes control and keeps API requests on the network.
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  expect((await page.request.get("/sw.js")).headers()["cache-control"]).toContain("no-cache");

  // The offline fallback page is precached; API responses never are.
  const cached = await page.evaluate(async () => {
    const urls: string[] = [];
    for (const name of await caches.keys()) {
      for (const request of await (await caches.open(name)).keys()) urls.push(new URL(request.url).pathname);
    }
    return urls;
  });
  expect(cached).toContain("/offline.html");
  expect(cached.filter((url) => url.startsWith("/api/"))).toEqual([]);

  // Without network the app explains the problem (offline page or shell with a retry), then recovers.
  await context.setOffline(true);
  try {
    await page.goto("/settings/account");
    await expect(
      page
        .getByRole("heading", { name: "You're offline · Нет соединения" })
        .or(page.getByText("Network error — check your connection.")),
    ).toBeVisible();
  } finally {
    await context.setOffline(false);
  }
  await page.goto("/");
  await expect(page.getByTestId("composer-input")).toBeVisible();
});
