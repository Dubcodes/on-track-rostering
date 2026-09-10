const SHELL_CACHE = "ontrack-shell-v2";
const ROSTER_PREFIX = "ontrack-roster-";
const SHELL = ["/static/style.css", "/static/branding.css", "/static/app.js", "/static/passkeys.js", "/static/notifications.js"];
const ACTIVE_USER_KEY = "/__ontrack_active_user";

async function setActiveUser(namespace) {
  const ownRosterCache = ROSTER_PREFIX + String(namespace);
  const keys = await caches.keys();
  await Promise.all(keys
    .filter((key) => key.startsWith(ROSTER_PREFIX) && key !== ownRosterCache)
    .map((key) => caches.delete(key)));
  const cache = await caches.open(SHELL_CACHE);
  await cache.put(ACTIVE_USER_KEY, new Response(String(namespace)));
}

async function activeRosterCache() {
  const marker = await (await caches.open(SHELL_CACHE)).match(ACTIVE_USER_KEY);
  if (!marker) return null;
  return caches.open(ROSTER_PREFIX + await marker.text());
}

function html(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character]);
}

async function offlineRosterPage() {
  const cache = await activeRosterCache();
  const response = cache ? await cache.match("/api/upcoming-work") : null;
  if (!response) return new Response("The roster is offline and no work has been saved for this account.", {status: 503, headers: {"Content-Type": "text/plain; charset=utf-8"}});
  const payload = await response.json();
  const productName = html(payload.product_name || "Roster");
  const rows = (payload.days || []).map((day) => `<li><strong>${html(day.date)}</strong> — ${html(day.track)} · ${html(day.role)} · ${html(day.start || "Start TBC")}</li>`).join("");
  const saved = html(payload.saved_at ? new Date(payload.saved_at).toLocaleString() : "unknown");
  return new Response(`<!doctype html><meta name="viewport" content="width=device-width"><title>${productName} offline</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/branding.css"><main class="page-shell"><h1>Upcoming work</h1><div class="offline-banner">Offline — showing roster saved at ${saved}</div><section class="panel"><ul>${rows || "<li>No upcoming work was saved.</li>"}</ul></section><p>Reconnect to view or edit the authoritative roster.</p></main>`, {headers: {"Content-Type": "text/html; charset=utf-8"}});
}

self.addEventListener("install", (event) => event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL))));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("message", (event) => {
  if (event.data?.type === "SET_USER" && /^[a-f0-9-]+$/i.test(String(event.data.namespace || ""))) {
    event.waitUntil(setActiveUser(event.data.namespace));
  }
  if (event.data?.type === "CLEAR_USER_CACHES") {
    event.waitUntil(Promise.all([
      caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith(ROSTER_PREFIX)).map((key) => caches.delete(key)))),
      caches.open(SHELL_CACHE).then((cache) => cache.delete(ACTIVE_USER_KEY))
    ]));
  }
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  const rosterPage = url.pathname === "/month" || /^\/day\/[a-f0-9-]+$/i.test(url.pathname);
  if (event.request.mode === "navigate" && rosterPage) {
    event.respondWith(fetch(event.request).catch(offlineRosterPage));
    return;
  }
  if (url.pathname === "/api/upcoming-work" || url.pathname.startsWith("/api/day/")) {
    event.respondWith(fetch(event.request).then(async (response) => {
      if (!response.ok) return response;
      const copy = response.clone();
      const payload = await copy.clone().json();
      const namespace = String(payload.user_namespace || "");
      if (!/^[a-f0-9-]+$/i.test(namespace)) return response;
      if (payload.offline_cacheable === false) return response;
      await setActiveUser(namespace);
      const cache = await caches.open(ROSTER_PREFIX + namespace);
      await cache.put(event.request, copy);
      return response;
    }).catch(async () => {
      const cache = await activeRosterCache();
      return cache ? cache.match(event.request) : Response.error();
    }));
    return;
  }
  if (SHELL.includes(url.pathname)) event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
self.addEventListener("push", (event) => {
  let payload = {title: "Roster update", body: "Open the roster for details.", url: "/month"};
  try { payload = {...payload, ...event.data.json()}; } catch (_) { /* use safe defaults */ }
  event.waitUntil(self.registration.showNotification(payload.title, {
    body: payload.body,
    data: {url: payload.url},
    tag: payload.event_key || "ontrack-update"
  }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/month";
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then((clients) => {
    const existing = clients.find((client) => new URL(client.url).pathname === url);
    return existing ? existing.focus() : self.clients.openWindow(url);
  }));
});
