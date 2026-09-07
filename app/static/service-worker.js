const SHELL_CACHE = "ontrack-shell-v1";
const ROSTER_PREFIX = "ontrack-roster-";
const SHELL = ["/static/style.css", "/static/app.js", "/manifest.webmanifest"];
const ACTIVE_USER_KEY = "/__ontrack_active_user";

async function setActiveUser(namespace) {
  const cache = await caches.open(SHELL_CACHE);
  await cache.put(ACTIVE_USER_KEY, new Response(String(namespace)));
}

async function activeRosterCache() {
  const marker = await (await caches.open(SHELL_CACHE)).match(ACTIVE_USER_KEY);
  if (!marker) return null;
  return caches.open(ROSTER_PREFIX + await marker.text());
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
  if (event.request.mode === "navigate" && (url.pathname === "/month" || url.pathname.startsWith("/day/"))) {
    event.respondWith(fetch(event.request).then(async (response) => {
      if (!response.ok) return response;
      const text = await response.clone().text();
      const match = text.match(/data-user-namespace="([a-f0-9-]+)"/i);
      if (match) {
        await setActiveUser(match[1]);
        await (await caches.open(ROSTER_PREFIX + match[1])).put(event.request, response.clone());
      }
      return response;
    }).catch(async () => {
      const userCache = await activeRosterCache();
      const cached = userCache ? await userCache.match(event.request) : null;
      if (cached) return cached;
      return new Response("On Track is offline and this page has not been saved on this device.", {
        status: 503, headers: {"Content-Type": "text/plain; charset=utf-8"}
      });
    }));
    return;
  }
  if (url.pathname.startsWith("/api/month") || url.pathname.startsWith("/api/day/")) {
    event.respondWith(fetch(event.request).then(async (response) => {
      if (!response.ok) return response;
      const copy = response.clone();
      const payload = await copy.clone().json();
      const namespace = String(payload.user_namespace || "unknown");
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
