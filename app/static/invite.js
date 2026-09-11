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
  }
})();
