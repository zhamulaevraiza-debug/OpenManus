/// <reference lib="webworker" />
/**
 * Service worker: precaches the app shell (hashed assets), serves navigations network-first with
 * an offline fallback page, and never touches /api.
 */
import { cleanupOutdatedCaches, matchPrecache, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { NetworkOnly } from "workbox-strategies";

declare const self: ServiceWorkerGlobalScope;

const OFFLINE_URL = "/offline.html";

// index.html is served network-first (below), never from the precache.
precacheAndRoute(
  self.__WB_MANIFEST.filter((entry) => !(typeof entry === "string" ? entry : entry.url).endsWith("index.html")),
);
cleanupOutdatedCaches();

const network = new NetworkOnly();

registerRoute(
  new NavigationRoute(
    async (options) => {
      try {
        return await network.handle(options);
      } catch {
        return (await matchPrecache(OFFLINE_URL)) ?? Response.error();
      }
    },
    { denylist: [/^\/api\//] },
  ),
);

self.addEventListener("install", () => {
  void self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data as { url?: string } | null)?.url ?? "/";
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const existing = windows.find(
        (client) => new URL(client.url).pathname === new URL(target, self.location.origin).pathname,
      );
      if (existing) {
        await existing.focus();
        return;
      }
      const any = windows[0];
      if (any) {
        await any.focus();
        await any.navigate(target);
        return;
      }
      await self.clients.openWindow(target);
    })(),
  );
});
