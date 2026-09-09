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
  document.querySelectorAll("[data-track-colour]").forEach((element) => {
    const colour = element.dataset.trackColour;
    if (/^#[0-9a-f]{6}$/i.test(colour || "")) element.style.setProperty("--track", colour);
  });
  document.querySelectorAll("[data-auto-submit]").forEach((element) => {
    element.addEventListener("change", () => element.form?.submit());
  });
  document.querySelectorAll("[data-crew-search]").forEach((search) => {
    search.addEventListener("input", () => {
      const query = search.value.trim().toLocaleLowerCase();
      document.querySelectorAll("[data-crew-picker] option").forEach((option) => {
        if (!option.value) return;
        option.hidden = Boolean(query) && !option.textContent.toLocaleLowerCase().includes(query);
      });
    });
  });
  let touchStart = null;
  document.getElementById("calendar-view")?.addEventListener("touchstart", (event) => {
    touchStart = event.changedTouches[0]?.clientX ?? null;
  }, {passive: true});
  document.getElementById("calendar-view")?.addEventListener("touchend", (event) => {
    if (touchStart === null) return;
    const distance = (event.changedTouches[0]?.clientX ?? touchStart) - touchStart;
    if (Math.abs(distance) > 70) {
      document.querySelector(distance < 0 ? '[aria-label="Next month"]' : '[aria-label="Previous month"]')?.click();
    }
    touchStart = null;
  }, {passive: true});
  if ("serviceWorker" in navigator && document.body.dataset.userNamespace) {
    navigator.serviceWorker.register("/service-worker.js").then(() => navigator.serviceWorker.ready).then((registration) => {
      registration.active?.postMessage({ type: "SET_USER", namespace: document.body.dataset.userNamespace });
    });
    const prefetch = async () => {
      let upcoming;
      try {
        const response = await fetch("/api/upcoming-work", {headers: {"X-OnTrack-Prefetch": "1"}});
        if (!response.ok) return;
        upcoming = await response.json();
        if (upcoming.saved_at) localStorage.setItem("ontrack-last-saved-at", upcoming.saved_at);
      } catch (_) { return; }
      const ids = [...new Set((upcoming.days || []).map((item) => item.id))].slice(0, 4);
      for (const id of ids) {
        try {
          const response = await fetch(`/api/day/${id}`, { headers: { "X-OnTrack-Prefetch": "1" } });
          if (!response.ok) break;
          const payload = await response.json();
          if (payload.saved_at) localStorage.setItem("ontrack-last-saved-at", payload.saved_at);
        }
        catch (_) { break; }
      }
    };
    window.setTimeout(prefetch, 400);
  }
})();
