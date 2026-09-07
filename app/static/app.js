(() => {
  "use strict";
  const switches = document.querySelectorAll("[data-view]");
  const calendar = document.getElementById("calendar-view");
  const list = document.getElementById("list-view");
  switches.forEach((button) => button.addEventListener("click", () => {
    const showList = button.dataset.view === "list";
    if (calendar) calendar.hidden = showList;
    if (list) list.hidden = !showList;
    switches.forEach((item) => item.classList.toggle("active", item === button));
    localStorage.setItem("ontrack-month-view", showList ? "list" : "calendar");
  }));
  if (localStorage.getItem("ontrack-month-view") === "list") {
    document.querySelector('[data-view="list"]')?.click();
  }
  const offline = document.getElementById("offline-banner");
  const updateOnline = () => {
    if (navigator.onLine) {
      localStorage.setItem("ontrack-last-saved-at", new Date().toISOString());
      if (offline) offline.hidden = true;
    } else if (offline) {
      const saved = localStorage.getItem("ontrack-last-saved-at");
      offline.textContent = saved
        ? `Offline — showing roster saved at ${new Date(saved).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}`
        : "Offline — showing the last roster saved on this device";
      offline.hidden = false;
    }
  };
  window.addEventListener("online", updateOnline);
  window.addEventListener("offline", updateOnline);
  updateOnline();
  if ("serviceWorker" in navigator && document.body.dataset.userNamespace) {
    navigator.serviceWorker.register("/service-worker.js").then(() => navigator.serviceWorker.ready).then((registration) => {
      registration.active?.postMessage({ type: "SET_USER", namespace: document.body.dataset.userNamespace });
    });
    const prefetch = async () => {
      const ids = [...new Set([...document.querySelectorAll('a[href^="/day/"]')]
        .map((link) => link.getAttribute("href")?.split("/").pop()).filter(Boolean))].slice(0, 3);
      for (const id of ids) {
        try { await fetch(`/api/day/${id}`, { headers: { "X-OnTrack-Prefetch": "1" } }); }
        catch (_) { break; }
      }
    };
    window.setTimeout(prefetch, 400);
  }
})();
