(() => {
  "use strict";
  const rosterNav = document.querySelector("[data-roster-nav]");
  const go = (url) => { if (url) window.location.href = url; };
  const isTyping = (event) => {
    const tag = event.target?.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target?.isContentEditable;
  };
  document.addEventListener("keydown", (event) => {
    if (!rosterNav || event.altKey || event.ctrlKey || event.metaKey || isTyping(event)) return;
    const key = event.key.toLowerCase();
    if (key === "m") go(rosterNav.dataset.monthUrl);
    if (key === "l") go(rosterNav.dataset.listUrl);
    if (key === "n") go(rosterNav.dataset.nextUrl);
    if (key === "p") go(rosterNav.dataset.prevUrl);
  });
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
  document.querySelectorAll("[data-position-search]").forEach((search) => {
    search.addEventListener("input", () => {
      const query = search.value.trim().toLocaleLowerCase();
      document.querySelectorAll('select[name="base_position_id"] option').forEach((option) => {
        if (!option.value) return;
        option.hidden = Boolean(query) && !option.textContent.toLocaleLowerCase().includes(query);
      });
    });
  });
  let touchStartX = 0;
  let touchStartY = 0;
  document.addEventListener("touchstart", (event) => {
    if (!rosterNav || event.touches.length !== 1) return;
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
  }, {passive: true});
  document.addEventListener("touchend", (event) => {
    if (!rosterNav || !touchStartX || !event.changedTouches.length) return;
    const deltaX = event.changedTouches[0].clientX - touchStartX;
    const deltaY = event.changedTouches[0].clientY - touchStartY;
    touchStartX = 0;
    touchStartY = 0;
    if (Math.abs(deltaX) < 70 || Math.abs(deltaX) < Math.abs(deltaY) * 1.4) return;
    go(deltaX < 0 ? rosterNav.dataset.nextUrl : rosterNav.dataset.prevUrl);
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
        localStorage.setItem("ontrack-last-saved-at", new Date().toISOString());
      } catch (_) { return; }
      const ids = [...new Set((upcoming.days || []).map((item) => item.id))];
      for (const id of ids) {
        try {
          const response = await fetch(`/api/day/${id}`, { headers: { "X-OnTrack-Prefetch": "1" } });
          if (!response.ok) break;
          await response.json();
          localStorage.setItem("ontrack-last-saved-at", new Date().toISOString());
        }
        catch (_) { break; }
      }
    };
    window.setTimeout(prefetch, 400);
  }
})();
