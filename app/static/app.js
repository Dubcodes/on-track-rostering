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
      if (form.matches("[data-inplace-save]")) return;
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
  document.addEventListener("click", (event) => {
    const help = event.target instanceof Element ? event.target.closest(".inline-help[tabindex]") : null;
    if (help instanceof HTMLElement) {
      if (!help.closest("summary")) event.preventDefault();
      help.focus({preventScroll: true});
    }
  });
  const filterRestoreKey = "ontrack-live-filter-position";
  try {
    const saved = JSON.parse(sessionStorage.getItem(filterRestoreKey) || "null");
    if (saved && saved.url === `${window.location.pathname}${window.location.search}`) {
      window.requestAnimationFrame(() => {
        window.scrollTo({top: saved.y, behavior: "instant"});
        if (saved.name) document.querySelector(`[name="${CSS.escape(saved.name)}"]`)?.focus({preventScroll: true});
      });
      sessionStorage.removeItem(filterRestoreKey);
    }
  } catch (_) { sessionStorage.removeItem(filterRestoreKey); }
  document.querySelectorAll("form[data-live-filters]").forEach((form) => {
    let timer = 0;
    const navigate = (replace = true) => {
      const url = new URL(form.action || window.location.href, window.location.href);
      url.search = "";
      new FormData(form).forEach((value, key) => {
        if (typeof value === "string" && value) url.searchParams.append(key, value);
      });
      url.hash = form.dataset.filterTarget || "";
      const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      sessionStorage.setItem(filterRestoreKey, JSON.stringify({
        url: `${url.pathname}${url.search}`,
        y: window.scrollY,
        name: focused?.getAttribute("name") || "",
      }));
      if (replace) window.location.replace(url);
      else window.location.assign(url);
    };
    form.querySelectorAll("select, input[type='checkbox'], input[type='radio'], input[type='date']").forEach((control) => {
      control.addEventListener("change", () => navigate(true));
    });
    form.querySelectorAll("input[type='search'], input[data-filter-search]").forEach((control) => {
      control.addEventListener("input", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => navigate(true), 350);
      });
      control.addEventListener("search", () => navigate(true));
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      window.clearTimeout(timer);
      navigate(false);
    });
  });
  document.querySelectorAll("[data-live-search]").forEach((root) => {
    const input = root.querySelector("[data-live-search-input]") || document.querySelector("[data-live-search-input]");
    const items = [...root.querySelectorAll("[data-live-search-item]")];
    const empty = root.querySelector("[data-live-search-empty]");
    if (!input) return;
    const filter = () => {
      const query = input.value.trim().toLocaleLowerCase();
      let visible = 0;
      items.forEach((item) => {
        const matches = !query || (item.dataset.search || item.textContent || "").toLocaleLowerCase().includes(query);
        item.hidden = !matches;
        if (matches) visible += 1;
      });
      if (empty) empty.hidden = visible !== 0;
    };
    input.addEventListener("input", filter);
    input.addEventListener("search", filter);
    filter();
  });
  document.querySelectorAll("[data-empty-build-url]").forEach((cell) => {
    const activate = (event) => {
      if (event.target instanceof Element && event.target.closest("a, button, input, select, textarea, details")) return;
      window.location.href = cell.dataset.emptyBuildUrl;
    };
    cell.addEventListener("click", activate);
    cell.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      activate(event);
    });
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
  document.querySelector("[data-select-ready]")?.addEventListener("change", (event) => {
    document.querySelectorAll("[data-ready-person]").forEach((item) => {
      item.checked = event.currentTarget.checked;
    });
  });
  document.querySelectorAll("[data-copy-setup-link]").forEach((button) => {
    button.addEventListener("click", () => {
      navigator.clipboard.writeText(button.closest("article").dataset.url);
    });
  });
  document.querySelectorAll("[data-copy-invitation-link]").forEach((button) => {
    button.addEventListener("click", async () => {
      const input = document.querySelector("[data-invitation-link]");
      if (!input) return;
      try {
        await navigator.clipboard.writeText(input.value);
        button.textContent = "Copied";
      } catch (_) {
        input.focus();
        input.select();
        button.textContent = "Select link to copy";
      }
    });
  });
  document.querySelector("[data-download-setup-links]")?.addEventListener("click", () => {
    const quote = (value) => `"${value.replaceAll('"', '""')}"`;
    const rows = [
      ["person", "email", "setup_url"],
      ...[...document.querySelectorAll("[data-setup-links] article")].map((item) => [
        item.dataset.person, item.dataset.email, item.dataset.url,
      ]),
    ];
    const blob = new Blob([rows.map((row) => row.map(quote).join(",")).join("\r\n")], {
      type: "text/csv",
    });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "on-track-setup-links.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  });
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
  const initialiseRoleRegionChoices = () => document.querySelectorAll("[data-role-region-choice]").forEach((choice) => {
    const form = choice.closest("form");
    const update = () => {
      const [role, region] = choice.value.split("|");
      form.querySelector("[data-role-choice]").value = role || "";
      form.querySelector("[data-region-choice]").value = region || "";
    };
    if (choice.dataset.initialised) return;
    choice.dataset.initialised = "true";
    choice.addEventListener("change", update);
    update();
  });
  initialiseRoleRegionChoices();
  const personEditor = document.querySelector("[data-person-editor]");
  if (personEditor) {
    const state = personEditor.querySelector("[data-save-state]");
    const save = personEditor.querySelector("[data-save]");
    const discard = personEditor.querySelector("[data-discard]");
    let changed = 0;
    const clean = () => {
      changed = 0;
      state.textContent = "Saved";
      save.hidden = true;
      discard.hidden = true;
    };
    personEditor.addEventListener("change", () => {
      changed += 1;
      state.textContent = `${changed} unsaved change${changed === 1 ? "" : "s"}`;
      save.hidden = false;
      discard.hidden = false;
    });
    personEditor.addEventListener("reset", () => window.setTimeout(clean));
    personEditor.addEventListener("submit", async (event) => {
      event.preventDefault();
      state.textContent = "Saving…";
      save.disabled = true;
      try {
        const response = await fetch(personEditor.action, {
          method: "POST",
          body: new FormData(personEditor),
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(await response.text());
        const result = await response.json();
        personEditor.querySelectorAll("input, textarea").forEach((control) => {
          if (control.type === "checkbox" || control.type === "radio") {
            control.defaultChecked = control.checked;
          } else {
            control.defaultValue = control.value;
          }
        });
        personEditor.querySelectorAll("option").forEach((option) => {
          option.defaultSelected = option.selected;
        });
        clean();
        if (result.refresh_url) {
          const refreshed = await fetch(result.refresh_url, { headers: { Accept: "text/html" } });
          if (refreshed.ok) {
            const page = new DOMParser().parseFromString(await refreshed.text(), "text/html");
            const replacement = page.querySelector("[data-person-account-panel]");
            const current = document.querySelector("[data-person-account-panel]");
            if (replacement && current) current.replaceWith(replacement);
            const label = page.querySelector("[data-person-setup-label]");
            if (label) document.querySelector("[data-person-setup-label]").textContent = label.textContent;
            initialiseRoleRegionChoices();
          }
        }
      } catch (_) {
        state.textContent = "Could not save — review and try again";
      } finally {
        save.disabled = false;
      }
    });
  }
  document.querySelectorAll("[data-capability-picker]").forEach((picker) => {
    const input = picker.querySelector("[data-capability-input]");
    const value = picker.querySelector("[data-capability-value]");
    const menu = picker.querySelector("[data-capability-menu]");
    const empty = picker.querySelector("[data-capability-empty]");
    const options = Array.from(picker.querySelectorAll("[data-capability-option]"));
    let active = -1;
    const visible = () => options.filter((option) => !option.hidden);
    const setActive = (index) => {
      const choices = visible();
      options.forEach((option) => option.classList.remove("is-active"));
      if (!choices.length) { active = -1; return; }
      active = (index + choices.length) % choices.length;
      choices[active].classList.add("is-active");
      choices[active].scrollIntoView({block: "nearest"});
    };
    const open = () => { menu.hidden = false; input.setAttribute("aria-expanded", "true"); };
    const close = () => { menu.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
    const choose = (option) => {
      input.value = option.dataset.label;
      value.value = option.dataset.value;
      close();
    };
    const filter = () => {
      const query = input.value.trim().toLowerCase();
      value.value = "";
      options.forEach((option) => { option.hidden = !option.dataset.label.toLowerCase().includes(query); });
      empty.hidden = visible().length !== 0;
      setActive(0);
    };
    input.addEventListener("focus", () => { filter(); open(); });
    input.addEventListener("input", () => { filter(); open(); });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { close(); return; }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); open(); setActive(active + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Enter" && active >= 0) {
        event.preventDefault(); choose(visible()[active]);
      }
    });
    options.forEach((option) => option.addEventListener("click", () => choose(option)));
    document.addEventListener("pointerdown", (event) => { if (!picker.contains(event.target)) close(); });
  });
  document.querySelectorAll("[data-capability-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const position = form.querySelector('[name="position_id"]');
      if (!position.value) return;
      form.action = `${form.dataset.actionBase}${position.value}`;
    });
  });
  document.querySelectorAll('select[name="role"]').forEach((select) => {
    if (select.querySelector('option[value="CASUAL"]')) return;
    const employee = select.querySelector('option[value="EMPLOYEE"]');
    const option = document.createElement("option");
    option.value = "CASUAL";
    option.textContent = "Casual";
    employee?.insertAdjacentElement("afterend", option);
  });
  document.querySelectorAll("[data-account-create]").forEach((form) => {
    const role = form.querySelector('[name="role"]');
    const region = form.querySelector('[name="region_id"]');
    const person = form.querySelector('[name="person_id"]');
    const refresh = () => {
      const global = role?.value === "ADMIN";
      const contractor = role?.value === "CONTRACTOR";
      if (region) {
        region.required = !global;
        region.disabled = global;
      }
      if (person) person.required = contractor;
      form.querySelector("[data-region-help]")?.toggleAttribute("hidden", !global);
      form.querySelector("[data-person-help]")?.toggleAttribute("hidden", !contractor);
    };
    role?.addEventListener("change", refresh);
    refresh();
  });
  const reauthDialog = document.querySelector("[data-reauth-dialog]");
  const reauthForm = reauthDialog?.querySelector("[data-reauth-form]");
  const reauthCredential = reauthDialog?.querySelector("[data-reauth-credential]");
  const reauthError = reauthDialog?.querySelector("[data-reauth-error]");
  let freshAuthTarget = null;
  let freshAuthSubmitter = null;
  const freshAuthValid = () => {
    const deadline = Date.parse(document.body.dataset.freshAuthExpiresAt || "");
    return document.body.dataset.freshAuth === "true" && Number.isFinite(deadline) && deadline > Date.now();
  };
  const requestFreshAuth = (form = null, submitter = null) => {
    if (!reauthDialog) return;
    freshAuthTarget = form;
    freshAuthSubmitter = submitter;
    reauthError.hidden = true;
    reauthError.textContent = "";
    reauthForm.querySelector("[data-reauth-confirm]").hidden = false;
    reauthCredential.value = "";
    reauthDialog.showModal();
    reauthCredential.focus();
  };
  const sensitiveAction = (form) => {
    const path = new URL(form.action, window.location.href).pathname;
    return /^\/admin\/(users|invitations)(\/|$)/.test(path)
      || /^\/manage\/accounts\/(link-person|invitations|contractors|people-invitations|signup-requests|grants)(\/|$)/.test(path)
      || /^\/manage\/crew\/[^/]+\/(setup-link|account-status|employment-type|credential-reset|devices\/revoke|sub-manager|notice-scope|grants)(\/|$)/.test(path);
  };
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== "post") return;
    const valueGuard = form.dataset.freshAuthSubmitValue;
    const needsFresh = sensitiveAction(form) || form.hasAttribute("data-fresh-auth") || (valueGuard && event.submitter?.value === valueGuard);
    if (form.dataset.freshActionReady === "true") {
      delete form.dataset.freshActionReady;
      return;
    }
    if (!needsFresh || freshAuthValid()) return;
    event.preventDefault();
    if (form.hasAttribute("data-account-create")) {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: {Accept: "application/json"},
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      if (response.status !== 403) {
        const html = await response.text();
        document.open();
        document.write(html);
        document.close();
        return;
      }
    }
    requestFreshAuth(form, event.submitter);
  });
  reauthForm?.addEventListener("submit", async (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const confirm = reauthForm.querySelector("[data-reauth-confirm]");
    confirm.disabled = true;
    reauthError.hidden = true;
    try {
      const payload = new FormData();
      payload.set("credential", reauthCredential.value);
      payload.set("csrf_token", document.querySelector('input[name="csrf_token"]')?.value || "");
      payload.set("next", `${window.location.pathname}${window.location.search}`);
      if (freshAuthTarget) payload.set("action", new URL(freshAuthTarget.action, window.location.href).pathname);
      const response = await fetch("/settings/reauthenticate", {
        method: "POST",
        body: payload,
        headers: {Accept: "application/json"},
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.detail || "Credential could not be verified.");
      reauthCredential.value = "";
      const target = freshAuthTarget;
      const submitter = freshAuthSubmitter;
      freshAuthTarget = null;
      freshAuthSubmitter = null;
      reauthDialog.close();
      if (target) {
        target.dataset.freshActionReady = "true";
        target.requestSubmit(submitter || undefined);
      } else {
        const url = new URL(window.location.href);
        url.searchParams.delete("reauth");
        window.location.replace(`${url.pathname}${url.search}${url.hash}`);
      }
    } catch (error) {
      reauthError.textContent = error.message || "Credential could not be verified.";
      reauthError.hidden = false;
      reauthCredential.focus();
      reauthCredential.select();
    } finally {
      confirm.disabled = false;
    }
  });
  if (new URLSearchParams(window.location.search).get("reauth") === "required") requestFreshAuth();
})();
