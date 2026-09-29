(() => {
  "use strict";
  const form = document.querySelector("[data-workday-form]");
  if (!form) return;
  const conflictDialog = form.querySelector("[data-assignment-conflict-dialog]");
  let pendingConflict = null;
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
        if (person.on_leave) {
          option.dataset.leaveStart = person.leave_start || "";
          option.dataset.leaveEnd = person.leave_end || "";
          option.dataset.leaveLabel = person.leave_label || "On leave";
        }
        const alreadySelected = [...list.querySelectorAll("[data-assignment-row]")].some((row) =>
          row !== picker.closest("[data-assignment-row]") && row.querySelector("[data-person-value]").value === person.id
        );
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
        if (alreadySelected) {
          const selected = document.createElement("small");
          const assignedRow = [...list.querySelectorAll("[data-assignment-row]")].find((row) =>
            row !== picker.closest("[data-assignment-row]") && row.querySelector("[data-person-value]").value === person.id
          );
          const assignedPosition = assignedRow?.querySelector('[data-picker-kind="position"] [data-picker-input]')?.value || "another position";
          selected.textContent = `Already assigned: ${assignedPosition}`;
          option.append(selected);
        }
        [person.context, person.hint, person.leave_label].filter(Boolean).forEach((detail) => {
          const small = document.createElement("small");
          small.textContent = detail;
          if (detail === person.leave_label) small.className = "leave-warning";
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
    if (form.dataset.newMode === "1") {
      const regionId = form.querySelector('[name="region_id"]').value;
      const workDate = form.querySelector('[name="work_date"]').value;
      if (!regionId || !workDate) {
        renderCrewGroups(picker, []);
        empty.textContent = "Choose a region and date for crew suggestions.";
        empty.hidden = false;
        return;
      }
    }
    const requestKey = `${positionId}-${Date.now()}`;
    row.dataset.crewPickerRequest = requestKey;
    renderCrewGroups(picker, []);
    empty.textContent = "Loading position-aware crew…";
    empty.hidden = false;
    const selectedPersonId = row.querySelector("[data-person-value]").value;
    const url = new URL(form.dataset.crewPickerUrl, window.location.origin);
    url.searchParams.set("position_id", positionId);
    if (form.dataset.newMode === "1") {
      url.searchParams.set("region_id", form.querySelector('[name="region_id"]').value);
      url.searchParams.set("work_date", form.querySelector('[name="work_date"]').value);
    }
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
  const copyPersonTravel = (source, target) => {
    const standard = source.querySelector("[data-standard-travel-check]").checked;
    target.querySelector("[data-standard-travel-check]").checked = standard;
    target.querySelector("[data-standard-travel-value]").value = standard ? "1" : "0";
    ["transport_mode", "vehicle_id"].forEach((name) => {
      target.querySelector(`[name="${name}"]`).value = source.querySelector(`[name="${name}"]`).value;
    });
    ["custom_transport_text", "accommodation_name", "hotel_to_track_minutes_override", "finish_destination_override", "return_travel_minutes_override"].forEach((name) => {
      target.querySelector(`[name="${name}"]`).value = source.querySelector(`[name="${name}"]`).value;
    });
    const sourceVehicle = source.querySelector('[data-picker-kind="vehicle"] [data-picker-input]');
    const targetVehicle = target.querySelector('[data-picker-kind="vehicle"] [data-picker-input]');
    const selectedLabel = sourceVehicle.dataset.selectedLabel ?? sourceVehicle.value;
    targetVehicle.value = selectedLabel;
    targetVehicle.dataset.selectedLabel = selectedLabel;
  };
  const projectPersonTravel = (row) => {
    const personId = row.querySelector("[data-person-value]").value;
    if (!personId) return;
    list.querySelectorAll("[data-assignment-row]").forEach((other) => {
      if (other !== row && other.querySelector("[data-person-value]").value === personId) {
        copyPersonTravel(row, other);
      }
    });
  };
  const applyPersonChoice = (picker, option) => {
    const row = picker.closest("[data-assignment-row]");
    const input = picker.querySelector("[data-picker-input]");
    input.value = option.dataset.label || "";
    input.dataset.selectedLabel = input.value;
    if (picker.dataset.pickerKind === "position") {
      row.querySelector("[data-position-value]").value = option.dataset.value || "";
      if (option.dataset.value) loadCrewPicker(row, option.dataset.value);
    } else if (picker.dataset.pickerKind === "person") {
      row.querySelector("[data-person-value]").value = option.dataset.value || "";
      row.querySelector("[data-assignment-state]").value = option.dataset.state || "ASSIGNED";
      const warning = row.querySelector("[data-assignment-leave]");
      warning.textContent = option.dataset.leaveLabel || "";
      warning.hidden = !option.dataset.leaveLabel;
    } else if (picker.dataset.pickerKind === "vehicle") {
      const mode = option.dataset.transportMode || (option.dataset.value ? "VEHICLE" : "UNASSIGNED");
      row.querySelector("[data-vehicle-value]").value = mode === "VEHICLE" ? option.dataset.value || "" : "";
      row.querySelector("[data-transport-value]").value = mode;
      if (mode !== "CUSTOM") row.querySelector('input[name="custom_transport_text"]').value = "";
      projectPersonTravel(row);
    }
    closePicker(picker);
    refreshRow(row);
  };
  const resetPersonRow = (row) => {
    row.querySelector("[data-person-value]").value = "";
    row.querySelector("[data-assignment-state]").value = "TBC";
    const input = row.querySelector('[data-picker-kind="person"] [data-picker-input]');
    input.value = "Unassigned";
    input.dataset.selectedLabel = "Unassigned";
    const warning = row.querySelector("[data-assignment-leave]");
    warning.textContent = "";
    warning.hidden = true;
    refreshRow(row);
  };
  const finishConflict = (action) => {
    if (!pendingConflict) return;
    const {picker, option, existingRow, input} = pendingConflict;
    if (action === "move" && existingRow) resetPersonRow(existingRow);
    if (action === "keep") {
      const targetRow = picker.closest("[data-assignment-row]");
      copyPersonTravel(existingRow, targetRow);
    }
    if (action !== "cancel") applyPersonChoice(picker, option);
    pendingConflict = null;
    conflictDialog.close();
    input.focus();
  };
  const choose = (picker, option) => {
    if (picker.dataset.pickerKind === "person" && option.dataset.value) {
      const row = picker.closest("[data-assignment-row]");
      const existingRow = [...list.querySelectorAll("[data-assignment-row]")].find((candidate) =>
        candidate !== row && candidate.querySelector("[data-person-value]").value === option.dataset.value
      );
      const onLeave = Boolean(option.dataset.leaveLabel);
      if (existingRow || onLeave) {
        const input = picker.querySelector("[data-picker-input]");
        pendingConflict = {picker, option, existingRow, input};
        const person = option.dataset.label || "This person";
        const position = existingRow?.querySelector('[data-picker-kind="position"] [data-picker-input]')?.value || "another position";
        const title = conflictDialog.querySelector("[data-conflict-title]");
        const message = conflictDialog.querySelector("[data-conflict-message]");
        const move = conflictDialog.querySelector("[data-conflict-move]");
        const keep = conflictDialog.querySelector("[data-conflict-keep]");
        if (existingRow && onLeave) {
          title.textContent = "Crew member conflict";
          message.textContent = `${person} is already assigned to ${position} and is ${option.dataset.leaveLabel.toLowerCase().replace(" · ", " from ")}.`;
          move.textContent = "Move to this position";
          keep.hidden = false;
        } else if (existingRow) {
          title.textContent = "Crew member already assigned";
          message.textContent = `${person} is already assigned to ${position}.`;
          move.textContent = "Move to this position";
          keep.hidden = false;
        } else {
          title.textContent = "Crew member on leave";
          message.textContent = `${person} is ${option.dataset.leaveLabel.toLowerCase().replace(" · ", " from ")}.`;
          move.textContent = "Roster anyway";
          keep.hidden = true;
        }
        closePicker(picker);
        conflictDialog.showModal();
        move.focus();
        return;
      }
    }
    applyPersonChoice(picker, option);
  };
  conflictDialog?.querySelector("[data-conflict-move]")?.addEventListener("click", () => finishConflict("move"));
  conflictDialog?.querySelector("[data-conflict-keep]")?.addEventListener("click", () => finishConflict("keep"));
  conflictDialog?.querySelector("[data-conflict-cancel]")?.addEventListener("click", () => finishConflict("cancel"));
  conflictDialog?.addEventListener("cancel", (event) => { event.preventDefault(); finishConflict("cancel"); });
  const wirePicker = (picker) => {
    const input = picker.querySelector("[data-picker-input]");
    input.dataset.selectedLabel = input.value;
    const clearStaleSelection = () => {
      if (input.value === (input.dataset.selectedLabel || "")) return;
      const row = picker.closest("[data-assignment-row]");
      if (picker.dataset.pickerKind === "position") {
        row.querySelector("[data-position-value]").value = "";
      } else if (picker.dataset.pickerKind === "person") {
        row.querySelector("[data-person-value]").value = "";
        row.querySelector("[data-assignment-state]").value = "TBC";
      } else if (picker.dataset.pickerKind === "vehicle") {
        row.querySelector("[data-vehicle-value]").value = "";
        row.querySelector("[data-transport-value]").value = "UNASSIGNED";
        input.dataset.selectedLabel = "";
        projectPersonTravel(row);
      }
      input.dataset.selectedLabel = "";
      refreshRow(row);
    };
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
    input.addEventListener("input", () => { clearStaleSelection(); open(); });
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
    const standardTravel = row.querySelector("[data-standard-travel-check]");
    standardTravel?.addEventListener("change", () => {
      row.querySelector("[data-standard-travel-value]").value = standardTravel.checked ? "1" : "0";
      projectPersonTravel(row);
    });
    ["custom_transport_text", "accommodation_name", "hotel_to_track_minutes_override", "finish_destination_override", "return_travel_minutes_override"].forEach((name) => {
      row.querySelector(`input[name="${name}"]`)?.addEventListener("change", () => projectPersonTravel(row));
    });
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
  const presets = {
    THOROUGHBRED: ["Side 1", "Side 2", "Start", "Head On", "Back", "Turn", "RTS", "Director", "Sound", "VT", "CCU1", "CCU2", "FM", "ENG"],
    HARNESS: ["Side 1", "Side 2", "Head On", "Back", "Director", "Sound/VT", "CCU1", "CCU2", "FM", "ENG"],
    TRIALS: ["Side 1", "Side 2", "Head On", "Back", "Director", "Sound/VT", "ENG"],
    BLANK: [],
  };
  const positionKey = (value) => normalize(value).replace(/[^a-z0-9]/g, "");
  const applyPreset = (allowReplace) => {
    if (list.children.length && !allowReplace) return;
    if (list.children.length && !window.confirm("Replace the current assignment rows with these defaults?")) return;
    list.replaceChildren();
    const unresolved = [];
    (presets[form.querySelector("[data-position-preset]").value] || []).forEach((label) => {
      const fragment = template.content.cloneNode(true);
      list.append(fragment);
      const row = list.lastElementChild;
      wireRow(row);
      const option = [...row.querySelectorAll('[data-picker-kind="position"] [data-picker-option]')]
        .find((item) => positionKey(item.dataset.label) === positionKey(label));
      if (option) choose(row.querySelector('[data-picker-kind="position"]'), option);
      else { unresolved.push(label); row.remove(); }
    });
    form.querySelector("[data-preset-warning]").textContent = unresolved.length ? `Missing from Master Data: ${unresolved.join(", ")}` : "";
  };
  form.querySelector("[data-apply-preset]")?.addEventListener("click", () => applyPreset(true));
  const dayType = form.querySelector("[data-day-type]");
  form.querySelector("[data-finish-input]")?.addEventListener("input", (event) => {
    form.querySelector("[data-finish-override]").value = event.currentTarget.value.trim() ? "1" : "0";
  });
  const syncDayType = () => {
    const [category, discipline] = dayType.value.split(":");
    const locationLabel = form.querySelector("[data-region-track]")?.closest("label")?.childNodes[0];
    if (locationLabel) locationLabel.textContent = ["RACE_DAY", "TRIALS"].includes(category) ? "Track" : "Location";
    form.querySelectorAll("[data-racing-time]").forEach((field) => { field.hidden = !["RACE_DAY", "TRIALS"].includes(category); });
    form.querySelectorAll("[data-race-time]").forEach((field) => { field.hidden = category !== "RACE_DAY"; });
    form.querySelectorAll("[data-trial-time]").forEach((field) => { field.hidden = category !== "TRIALS"; });
    const standardTravelAvailable = ["RACE_DAY", "TRIALS"].includes(category);
    const standardTravel = form.querySelector("[data-standard-plan]");
    form.querySelector("[data-standard-plan-control]")?.toggleAttribute("hidden", !standardTravelAvailable);
    form.querySelector("[data-standard-plan-unavailable]")?.toggleAttribute("hidden", standardTravelAvailable);
    if (standardTravel) {
      standardTravel.disabled = !standardTravelAvailable;
      if (!standardTravelAvailable) standardTravel.checked = false;
    }
    form.querySelector("[data-position-preset]").value = category === "TRIALS" ? "TRIALS" : (category === "RACE_DAY" ? discipline : "BLANK");
  };
  dayType?.addEventListener("change", syncDayType);
  syncDayType();
  const refreshNewModeCrew = () => {
    if (form.dataset.newMode !== "1") return;
    list.querySelectorAll("[data-assignment-row]").forEach((row) => {
      const positionId = row.querySelector("[data-position-value]").value;
      if (positionId) loadCrewPicker(row, positionId);
    });
  };
  form.querySelector('[name="region_id"]')?.addEventListener("change", refreshNewModeCrew);
  form.querySelector('[name="work_date"]')?.addEventListener("change", refreshNewModeCrew);
  if (form.dataset.newMode === "1" && !list.children.length) applyPreset(false);
  document.addEventListener("pointerdown", (event) => document.querySelectorAll("[data-search-picker]").forEach((picker) => { if (!picker.contains(event.target)) closePicker(picker); }));
})();
