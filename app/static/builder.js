(() => {
  "use strict";
  const form = document.querySelector("[data-workday-form]");
  if (!form) return;
  const list = form.querySelector("[data-assignment-list]");
  const template = form.querySelector("[data-assignment-template]");
  let openPicker = null;
  let positioningListenersAttached = false;
  const normalize = (value) => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const viewport = () => {
    const visual = window.visualViewport;
    return {
      top: visual?.offsetTop || 0,
      left: visual?.offsetLeft || 0,
      width: visual?.width || window.innerWidth,
      height: visual?.height || window.innerHeight,
    };
  };
  const positionPicker = (picker) => {
    const menu = picker.querySelector("[data-picker-menu]");
    const input = picker.querySelector("[data-picker-input]");
    if (menu.hidden) return;
    const rect = input.getBoundingClientRect();
    const view = viewport();
    const margin = 8;
    const gap = 4;
    const viewRight = view.left + view.width;
    const viewBottom = view.top + view.height;
    const width = Math.min(Math.max(rect.width, 280), Math.max(0, view.width - margin * 2));
    const left = Math.min(Math.max(rect.left, view.left + margin), viewRight - width - margin);
    const below = viewBottom - rect.bottom - gap - margin;
    const above = rect.top - view.top - gap - margin;
    const desired = Math.min(menu.scrollHeight, 360);
    let placement = "below";
    let available = below;
    if (below < Math.min(160, desired) && above > below) {
      placement = "above";
      available = above;
    }
    let maxHeight = Math.max(0, Math.min(360, available));
    let top;
    if (maxHeight >= 112) {
      top = placement === "below"
        ? rect.bottom + gap
        : rect.top - gap - Math.min(desired, maxHeight);
    } else {
      placement = "sheet";
      top = view.top + margin;
      maxHeight = Math.max(96, view.height - margin * 2);
    }
    Object.assign(menu.style, {
      position: "fixed",
      top: `${Math.round(top)}px`,
      left: `${Math.round(left)}px`,
      right: "auto",
      width: `${Math.round(width)}px`,
      maxWidth: "none",
      maxHeight: `${Math.round(maxHeight)}px`,
    });
    menu.dataset.placement = placement;
  };
  const repositionOpenPicker = () => { if (openPicker) positionPicker(openPicker); };
  const addPositioningListeners = () => {
    if (positioningListenersAttached) return;
    window.addEventListener("resize", repositionOpenPicker);
    window.addEventListener("scroll", repositionOpenPicker, true);
    window.visualViewport?.addEventListener("resize", repositionOpenPicker);
    window.visualViewport?.addEventListener("scroll", repositionOpenPicker);
    positioningListenersAttached = true;
  };
  const removePositioningListeners = () => {
    if (!positioningListenersAttached) return;
    window.removeEventListener("resize", repositionOpenPicker);
    window.removeEventListener("scroll", repositionOpenPicker, true);
    window.visualViewport?.removeEventListener("resize", repositionOpenPicker);
    window.visualViewport?.removeEventListener("scroll", repositionOpenPicker);
    positioningListenersAttached = false;
  };
  const closePicker = (picker) => {
    const menu = picker.querySelector("[data-picker-menu]");
    menu.hidden = true;
    menu.removeAttribute("data-placement");
    menu.removeAttribute("style");
    picker.querySelector("[data-picker-input]").setAttribute("aria-expanded", "false");
    if (openPicker === picker) {
      openPicker = null;
      removePositioningListeners();
    }
  };
  const visibleOptions = (picker) => [...picker.querySelectorAll("[data-picker-option]")].filter((item) => !item.hidden);
  const setActive = (picker, option) => {
    picker.querySelectorAll("[data-picker-option]").forEach((item) => item.classList.toggle("is-active", item === option));
    if (!option) return;
    const menu = picker.querySelector("[data-picker-menu]");
    const optionTop = option.offsetTop;
    const optionBottom = optionTop + option.offsetHeight;
    if (optionTop < menu.scrollTop) menu.scrollTop = optionTop;
    else if (optionBottom > menu.scrollTop + menu.clientHeight) {
      menu.scrollTop = optionBottom - menu.clientHeight;
    }
  };
  const filter = (picker) => {
    const needle = normalize(picker.querySelector("[data-picker-input]").value);
    let count = 0;
    picker.querySelectorAll("[data-picker-option]").forEach((option) => {
      const haystack = normalize(`${option.dataset.label || ""} ${option.dataset.search || ""}`);
      option.hidden = Boolean(needle) && !haystack.includes(needle);
      if (!option.hidden) count += 1;
    });
    picker.querySelectorAll(".search-picker-group").forEach((group) => { group.hidden = !group.querySelector("[data-picker-option]:not([hidden])"); });
    picker.querySelector("[data-picker-empty]").hidden = count > 0;
    setActive(picker, visibleOptions(picker)[0]);
  };
  const refreshRow = (row) => {
    const state = row.querySelector("[data-assignment-state]").value;
    row.classList.toggle("is-open", state === "OPEN");
    row.classList.toggle("is-tbc", state === "TBC" || state === "MANAGER_ACTION_REQUIRED");
    row.classList.toggle("needs-manager-action", state === "MANAGER_ACTION_REQUIRED");
  };
  const renderCrewGroups = (picker, groups) => {
    const container = picker.querySelector("[data-picker-groups]");
    if (!container) return;
    container.replaceChildren();
    groups.forEach((group) => {
      if (!group.people.length) return;
      const wrapper = document.createElement("div");
      wrapper.className = "search-picker-group";
      const heading = document.createElement("span");
      heading.textContent = group.label;
      wrapper.append(heading);
      group.people.forEach((person) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "search-picker-option";
        option.setAttribute("role", "option");
        option.dataset.pickerOption = "";
        option.dataset.value = person.id;
        option.dataset.state = "ASSIGNED";
        option.dataset.label = person.label;
        option.dataset.search = `${person.label} ${person.context || ""} ${person.hint || ""}`;
        const name = document.createElement("strong");
        name.textContent = person.label;
        if (person.same_date) {
          const warning = document.createElement("span");
          warning.className = "same-date-warning";
          warning.setAttribute("role", "img");
          warning.setAttribute("aria-label", "Already rostered on this date");
          warning.title = "Already rostered on this date";
          warning.textContent = "!";
          name.append(" ", warning);
        }
        option.append(name);
        [person.context, person.hint].filter(Boolean).forEach((detail) => {
          const small = document.createElement("small");
          small.textContent = detail;
          option.append(small);
        });
        wrapper.append(option);
      });
      container.append(wrapper);
    });
  };
  const loadCrewPicker = async (row, positionId) => {
    const picker = row.querySelector('[data-picker-kind="person"]');
    const empty = picker.querySelector("[data-picker-empty]");
    const requestKey = `${positionId}-${Date.now()}`;
    row.dataset.crewPickerRequest = requestKey;
    renderCrewGroups(picker, []);
    empty.textContent = "Loading position-aware crew…";
    empty.hidden = false;
    const selectedPersonId = row.querySelector("[data-person-value]").value;
    const url = new URL(form.dataset.crewPickerUrl, window.location.origin);
    url.searchParams.set("position_id", positionId);
    if (selectedPersonId) url.searchParams.set("person_id", selectedPersonId);
    try {
      const response = await fetch(url, {headers: {Accept: "application/json"}});
      if (!response.ok) throw new Error("Crew suggestions unavailable");
      const payload = await response.json();
      if (row.dataset.crewPickerRequest !== requestKey) return;
      renderCrewGroups(picker, payload.groups || []);
      empty.textContent = "No matching results";
      empty.hidden = true;
    } catch (_) {
      if (row.dataset.crewPickerRequest !== requestKey) return;
      empty.textContent = "Crew suggestions unavailable. Refresh and try again.";
      empty.hidden = false;
    }
  };
  const choose = (picker, option) => {
    const row = picker.closest("[data-assignment-row]");
    const input = picker.querySelector("[data-picker-input]");
    input.value = option.dataset.label || "";
    if (picker.dataset.pickerKind === "position") {
      row.querySelector("[data-position-value]").value = option.dataset.value || "";
      if (option.dataset.value) loadCrewPicker(row, option.dataset.value);
    } else if (picker.dataset.pickerKind === "person") {
      row.querySelector("[data-person-value]").value = option.dataset.value || "";
      row.querySelector("[data-assignment-state]").value = option.dataset.state || "ASSIGNED";
    } else if (picker.dataset.pickerKind === "transport") {
      row.querySelector("[data-transport-value]").value = option.dataset.value || "UNASSIGNED";
      if (option.dataset.value !== "VEHICLE") {
        row.querySelector("[data-vehicle-value]").value = "";
        const vehicleInput = row.querySelector('[data-picker-kind="vehicle"] [data-picker-input]');
        if (vehicleInput) vehicleInput.value = "Select vehicle";
      }
      if (option.dataset.value !== "CUSTOM") {
        const customInput = row.querySelector('input[name="custom_transport_text"]');
        if (customInput) customInput.value = "";
      }
    } else if (picker.dataset.pickerKind === "vehicle") {
      row.querySelector("[data-vehicle-value]").value = option.dataset.value || "";
      row.querySelector("[data-transport-value]").value = option.dataset.value ? "VEHICLE" : "UNASSIGNED";
      const transportInput = row.querySelector('[data-picker-kind="transport"] [data-picker-input]');
      if (transportInput) transportInput.value = option.dataset.value ? "Vehicle" : "No transport assigned yet";
    }
    closePicker(picker);
    refreshRow(row);
  };
  const wirePicker = (picker) => {
    const input = picker.querySelector("[data-picker-input]");
    const open = () => {
      document.querySelectorAll("[data-search-picker]").forEach((other) => { if (other !== picker) closePicker(other); });
      picker.querySelector("[data-picker-menu]").hidden = false;
      input.setAttribute("aria-expanded", "true");
      filter(picker);
      openPicker = picker;
      addPositioningListeners();
      positionPicker(picker);
      window.requestAnimationFrame(() => positionPicker(picker));
    };
    input.addEventListener("focus", () => { open(); input.select(); });
    input.addEventListener("input", open);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closePicker(picker);
        return;
      }
      if (event.key === "Tab") {
        closePicker(picker);
        return;
      }
      const options = visibleOptions(picker);
      if (!options.length) return;
      const active = picker.querySelector(".is-active");
      let index = Math.max(0, options.indexOf(active));
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        index = event.key === "ArrowDown" ? Math.min(options.length - 1, index + 1) : Math.max(0, index - 1);
        setActive(picker, options[index]);
      } else if (event.key === "Enter") {
        event.preventDefault();
        choose(picker, active || options[0]);
      }
    });
    picker.querySelector("[data-picker-menu]").addEventListener("click", (event) => {
      const option = event.target.closest("[data-picker-option]");
      if (option) choose(picker, option);
    });
  };
  const wireTimeInput = (input) => {
    const normalizeTime = () => {
      let value = input.value.trim();
      if (/^\d{3,4}$/.test(value)) value = `${value.slice(0, -2)}:${value.slice(-2)}`;
      const match = /^(\d{1,2}):(\d{2})$/.exec(value);
      if (match && Number(match[1]) < 24 && Number(match[2]) < 60) input.value = `${match[1].padStart(2, "0")}:${match[2]}`;
    };
    input.addEventListener("blur", normalizeTime);
    form.addEventListener("submit", normalizeTime);
  };
  const wireRow = (row) => {
    row.querySelectorAll("[data-search-picker]").forEach(wirePicker);
    row.querySelectorAll("[data-time-input]").forEach(wireTimeInput);
    row.querySelector("[data-toggle-advanced]")?.addEventListener("click", () => { row.querySelector("[data-assignment-advanced]").open = !row.querySelector("[data-assignment-advanced]").open; });
    row.querySelector("[data-remove-row]")?.addEventListener("click", () => row.remove());
    const privateCheck = row.querySelector("[data-private-check]");
    privateCheck?.addEventListener("change", () => { row.querySelector("[data-private-value]").value = privateCheck.checked ? "1" : "0"; });
    refreshRow(row);
  };
  list.querySelectorAll("[data-assignment-row]").forEach(wireRow);
  form.querySelector("[data-add-position]")?.addEventListener("click", () => {
    const fragment = template.content.cloneNode(true);
    list.append(fragment);
    const row = list.lastElementChild;
    wireRow(row);
    row.querySelector('[data-picker-kind="position"] [data-picker-input]').focus();
  });
  document.addEventListener("pointerdown", (event) => document.querySelectorAll("[data-search-picker]").forEach((picker) => { if (!picker.contains(event.target)) closePicker(picker); }));
})();
