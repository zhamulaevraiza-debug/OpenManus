/** System notifications while the app is in the background. */

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export async function requestNotificationPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!notificationsSupported()) return "unsupported";
  try {
    return await Notification.requestPermission();
  } catch {
    return Notification.permission;
  }
}

/** Shows a notification only if permitted and the page is hidden. */
export async function notifyInBackground(title: string, options: { body?: string; tag?: string } = {}): Promise<void> {
  if (!notificationsSupported() || Notification.permission !== "granted") return;
  if (document.visibilityState !== "hidden") return;
  const payload: NotificationOptions = {
    body: options.body,
    tag: options.tag,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { url: window.location.href },
  };
  try {
    const registration = await navigator.serviceWorker?.getRegistration();
    if (registration) {
      await registration.showNotification(title, payload);
      return;
    }
    const notification = new Notification(title, payload);
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Some browsers (e.g. Android Chrome without a service worker) refuse page notifications.
  }
}
