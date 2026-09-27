(() => {
  "use strict";
  const scrollKey = "ontrack-post-scroll";
  if (!window.location.hash) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(scrollKey) || "null");
      if (saved && saved.path === window.location.pathname && Date.now() - saved.at < 15000) {
        window.requestAnimationFrame(() => window.scrollTo({top: saved.y, behavior: "instant"}));
      }
      sessionStorage.removeItem(scrollKey);
    } catch (_) { sessionStorage.removeItem(scrollKey); }
  }
  document.querySelectorAll('form[method="post" i]').forEach((form) => {
    form.addEventListener("submit", () => {
      sessionStorage.setItem(scrollKey, JSON.stringify({
        path: window.location.pathname,
        y: window.scrollY,
        at: Date.now(),
      }));
    });
  });
  document.querySelectorAll("[data-region-track-form]").forEach((form) => {
    const region = form.querySelector("[data-track-region]");
    const track = form.querySelector("[data-region-track]");
    const options = form.parentElement.querySelector("[data-region-track-options]");
    if (!region || !track || !options) return;
    const refresh = () => {
      const previous = track.value;
      track.replaceChildren(new Option("To be confirmed", ""));
      options.content.querySelectorAll("option").forEach((option) => {
        if (option.dataset.regionId === region.value) track.append(option.cloneNode(true));
      });
      track.value = Array.from(track.options).some((option) => option.value === previous) ? previous : "";
    };
    region.addEventListener("change", refresh);
    refresh();
  });
  // Server-side parse_time is authoritative; this is just a display convenience.
  document.querySelectorAll("[data-time-input]").forEach((input) => {
    const normalize = () => {
      let value = input.value.trim();
      if (/^\d{3,4}$/.test(value)) value = `${value.slice(0, -2)}:${value.slice(-2)}`;
      const match = /^(\d{1,2}):(\d{2})$/.exec(value);
      if (match && Number(match[1]) < 24 && Number(match[2]) < 60) {
        input.value = `${match[1].padStart(2, "0")}:${match[2]}`;
      }
    };
    input.addEventListener("blur", normalize);
    input.form?.addEventListener("submit", normalize);
  });
  const rosterNav = document.querySelector("[data-roster-nav]");
  const dayNav = document.querySelector("[data-day-nav]");
  const go = (url) => { if (url) window.location.href = url; };
  const isTyping = (event) => {
    const tag = event.target?.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target?.isContentEditable;
  };
  document.addEventListener("keydown", (event) => {
    if ((!rosterNav && !dayNav) || event.altKey || event.ctrlKey || event.metaKey || isTyping(event)) return;
    const key = event.key.toLowerCase();
    if (key === "m") go(rosterNav?.dataset.monthUrl);
    if (key === "l") go(rosterNav?.dataset.listUrl);
    if (key === "n") go((dayNav || rosterNav)?.dataset.nextUrl);
    if (key === "p") go((dayNav || rosterNav)?.dataset.prevUrl);
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
  document.querySelectorAll("[data-auto-submit]").forEach((element) => {
    element.addEventListener("change", () => element.form?.submit());
  });
  let touchStartX = 0;
  let touchStartY = 0;
  let touchEligible = false;
  let lastDaySwipe = {direction: "", at: 0};
  let daySwipeHintTimer = 0;
  const showDaySwipeHint = (direction) => {
    const hint = dayNav?.querySelector("[data-day-swipe-hint]");
    if (!hint) return;
    hint.textContent = `Swipe again for ${direction === "next" ? "next" : "previous"} rostered day`;
    hint.hidden = false;
    window.clearTimeout(daySwipeHintTimer);
    daySwipeHintTimer = window.setTimeout(() => { hint.hidden = true; }, 1500);
  };
  document.addEventListener("touchstart", (event) => {
    if ((!rosterNav && !dayNav) || event.touches.length !== 1) return;
    touchEligible = !(event.target instanceof Element && event.target.closest("a, button, input, select, textarea, summary, [role='button']"));
    if (!touchEligible) return;
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
  }, {passive: true});
  document.addEventListener("touchend", (event) => {
    if ((!rosterNav && !dayNav) || !touchEligible || !touchStartX || !event.changedTouches.length) return;
    const deltaX = event.changedTouches[0].clientX - touchStartX;
    const deltaY = event.changedTouches[0].clientY - touchStartY;
    touchStartX = 0;
    touchStartY = 0;
    if (Math.abs(deltaX) < 70 || Math.abs(deltaX) < Math.abs(deltaY) * 1.4) return;
    const direction = deltaX < 0 ? "next" : "prev";
    if (dayNav) {
      const destination = direction === "next" ? dayNav.dataset.nextUrl : dayNav.dataset.prevUrl;
      if (!destination) return;
      const now = Date.now();
      if (lastDaySwipe.direction === direction && now - lastDaySwipe.at <= 1300) {
        lastDaySwipe = {direction: "", at: 0};
        go(destination);
      } else {
        lastDaySwipe = {direction, at: now};
        showDaySwipeHint(direction);
      }
      return;
    }
    go(deltaX < 0 ? rosterNav.dataset.nextUrl : rosterNav.dataset.prevUrl);
  }, {passive: true});
  if ("serviceWorker" in navigator && document.body.dataset.userNamespace) {
    const buildId = document.body.dataset.buildId || "local";
    const workerUrl = `/service-worker.js?v=${encodeURIComponent(buildId)}`;
    navigator.serviceWorker.register(workerUrl).then(() => navigator.serviceWorker.ready).then((registration) => {
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
