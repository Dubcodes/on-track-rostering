(() => {
  "use strict";
  const tokenInput = document.querySelector("[data-invite-token]");
  const form = document.querySelector("[data-invite-form]");
  const warning = document.querySelector("[data-invite-missing]");
  const submit = document.querySelector("[data-invite-submit]");
  if (!tokenInput || !form) return;
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get("token") || tokenInput.value;
  if (window.location.hash) window.history.replaceState(null, "", "/invite");
  tokenInput.value = token;
  if (!token) {
    if (warning) warning.hidden = false;
    if (submit) submit.disabled = true;
    return;
  }
  const load = async () => {
    const payload = new FormData();
    payload.set("token", token);
    try {
      const response = await fetch("/invite/details", { method: "POST", body: payload, headers: { Accept: "application/json" } });
      const result = await response.json();
      if (!response.ok || !result.available) throw new Error();
      document.querySelector("[data-invite-email]").value = result.email;
      const name = document.querySelector("[data-invite-name]");
      if (!name.value) name.value = result.display_name;
      document.querySelector("[data-invite-credential-help]").textContent = result.credential_help;
    } catch (_) {
      if (warning) { warning.hidden = false; warning.textContent = "This invitation is unavailable or has expired. Ask the sender for a new link."; }
      if (submit) submit.disabled = true;
    }
  };
  load();
  document.querySelectorAll("[data-secret-toggle]").forEach((button) => button.addEventListener("click", () => {
    const input = button.parentElement.querySelector("[data-secret-input]");
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    button.textContent = showing ? "Show" : "Hide";
    button.setAttribute("aria-label", `${showing ? "Show" : "Hide"} PIN or password`);
  }));
})();
