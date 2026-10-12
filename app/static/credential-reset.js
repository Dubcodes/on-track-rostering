(() => {
  "use strict";
  const tokenInput = document.querySelector("[data-credential-reset-token]");
  const form = document.querySelector("[data-credential-reset-form]");
  const warning = document.querySelector("[data-credential-reset-missing]");
  const submit = document.querySelector("[data-credential-reset-submit]");
  if (!tokenInput || !form) return;
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get("token") || tokenInput.value;
  if (window.location.hash) window.history.replaceState(null, "", "/credential-reset");
  tokenInput.value = token;
  if (!token) {
    if (warning) warning.hidden = false;
    if (submit) submit.disabled = true;
  }
})();
