// Gulf Pulse service worker: shows edition notifications and opens the app when tapped.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(d.title || "Gulf Pulse", {
    body: d.body || "A new edition is ready.",
    icon: "/icon-192.png",
    badge: "/badge-96.png",
    tag: d.tag || "gulf-pulse",
    renotify: true,
    data: { url: d.url || "/" }
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) { if (w.url.startsWith(self.location.origin)) { await w.focus(); w.postMessage({ type: "refresh" }); return; } }
    await self.clients.openWindow(url);
  })());
});
