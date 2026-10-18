(() => {
  "use strict";

  const slug = (value) => value.normalize("NFKD").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  const prepareSourceRecords = (root = document) => {
    root.querySelectorAll("[data-source-record]").forEach((item) => {
      const provider = item.dataset.provider || "source";
      const source = item.dataset.sourceName || "item";
      item.id ||= `source-${slug(`${provider}-${source}`)}`;
      item.querySelectorAll("form").forEach((form) => {
        form.dataset.refreshTarget ||= item.id;
      });
    });
  };

  const updateTrackContext = (select) => {
    const form = select.closest("form");
    const selected = select.selectedOptions[0];
    const region = form?.querySelector("[data-track-region-context]");
    const link = form?.querySelector("[data-track-manage-link]");
    if (region) {
      region.textContent = selected?.value
        ? `Canonical Region: ${selected.dataset.regionName}. No second map Region is required.`
        : "Selecting a Track uses its canonical Region automatically.";
    }
    if (link) {
      link.hidden = !selected?.value;
      if (selected?.dataset.manageUrl) link.href = selected.dataset.manageUrl;
    }
  };

  const feedback = (container, message, kind) => {
    let node = container.querySelector(":scope > [data-save-feedback]");
    if (!node) {
      node = document.createElement("p");
      node.dataset.saveFeedback = "";
      node.setAttribute("role", kind === "danger" ? "alert" : "status");
      const summary = container.querySelector(":scope > summary");
      if (summary) summary.insertAdjacentElement("afterend", node);
      else container.prepend(node);
    }
    node.className = `alert ${kind}`;
    node.textContent = message;
    return node;
  };

  const errorMessage = async (response) => {
    try {
      const payload = await response.clone().json();
      if (typeof payload.detail === "string") return payload.detail;
    } catch (_) { /* HTML or plain-text response. */ }
    const text = (await response.text()).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    return text || "The change could not be saved. Review the values and try again.";
  };

  prepareSourceRecords();
  document.querySelectorAll("[data-canonical-track]").forEach(updateTrackContext);

  document.addEventListener("change", (event) => {
    if (event.target.matches?.("[data-canonical-track]")) updateTrackContext(event.target);
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest?.("form[data-inplace-save]");
    if (!form || !form.checkValidity()) return;
    event.preventDefault();

    const targetId = form.dataset.refreshTarget;
    const target = targetId ? document.getElementById(targetId) : form.closest("details, section");
    const buttons = [...form.querySelectorAll("button, input[type='submit']")];
    const originalTop = target?.getBoundingClientRect().top;
    buttons.forEach((button) => { button.disabled = true; });
    if (target) feedback(target, "Saving…", "notice");

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {Accept: "text/html", "X-OnTrack-Inplace": "1"},
      });
      if (!response.ok) throw new Error(await errorMessage(response));

      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const replacement = targetId ? parsed.getElementById(targetId) : null;
      const message = form.dataset.successMessage || "Saved.";

      if (target && replacement) {
        if (replacement instanceof HTMLDetailsElement) replacement.open = true;
        feedback(replacement, message, "success");
        target.replaceWith(replacement);
        prepareSourceRecords(replacement);
        replacement.querySelectorAll("[data-canonical-track]").forEach(updateTrackContext);
        if (typeof originalTop === "number") {
          window.scrollBy(0, replacement.getBoundingClientRect().top - originalTop);
        }
      } else if (target && form.hasAttribute("data-source-completion")) {
        const currentConfirmed = document.getElementById("confirmed-source-mappings");
        const updatedConfirmed = parsed.getElementById("confirmed-source-mappings");
        if (currentConfirmed && updatedConfirmed) currentConfirmed.replaceWith(updatedConfirmed);
        target.open = true;
        target.querySelectorAll("form, details.create-missing-track").forEach((node) => node.remove());
        feedback(target, `${message} It now appears under Confirmed source mappings.`, "success");
      } else if (target) {
        feedback(target, message, "success");
      }
    } catch (error) {
      if (target) {
        target.open = true;
        feedback(target, error.message || "The change could not be saved.", "danger");
      }
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  });

  if (window.location.hash) {
    const target = document.getElementById(window.location.hash.slice(1));
    if (target) {
      if (target instanceof HTMLDetailsElement) target.open = true;
      target.closest("details")?.setAttribute("open", "");
      window.requestAnimationFrame(() => target.scrollIntoView({block: "center"}));
    }
  }
})();
