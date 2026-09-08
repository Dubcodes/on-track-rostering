(() => {
  "use strict";
  const fromBase64url = (value) => {
    const base64 = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4);
    return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  };
  const toBase64url = (value) => {
    if (!value) return null;
    const bytes = new Uint8Array(value);
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  };
  const prepareCreation = (options) => {
    options.challenge = fromBase64url(options.challenge);
    options.user.id = fromBase64url(options.user.id);
    (options.excludeCredentials || []).forEach((item) => { item.id = fromBase64url(item.id); });
    return options;
  };
  const prepareRequest = (options) => {
    options.challenge = fromBase64url(options.challenge);
    (options.allowCredentials || []).forEach((item) => { item.id = fromBase64url(item.id); });
    return options;
  };
  const registrationJSON = (credential) => ({
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(credential.response.clientDataJSON),
      attestationObject: toBase64url(credential.response.attestationObject),
      transports: credential.response.getTransports?.() || []
    },
    clientExtensionResults: credential.getClientExtensionResults()
  });
  const authenticationJSON = (credential) => ({
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(credential.response.clientDataJSON),
      authenticatorData: toBase64url(credential.response.authenticatorData),
      signature: toBase64url(credential.response.signature),
      userHandle: toBase64url(credential.response.userHandle)
    },
    clientExtensionResults: credential.getClientExtensionResults()
  });

  const registration = document.getElementById("passkey-register");
  registration?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = registration.querySelector("[data-passkey-status]");
    try {
      const form = new FormData(registration);
      const response = await fetch("/settings/passkeys/options", { method: "POST", body: form });
      if (!response.ok) throw new Error("Re-authenticate first, then try again.");
      const options = await response.json();
      const challengeId = options.challenge_id;
      delete options.challenge_id;
      const credential = await navigator.credentials.create({ publicKey: prepareCreation(options) });
      const verified = await fetch("/settings/passkeys/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          csrf_token: form.get("csrf_token"),
          challenge_id: challengeId,
          label: form.get("label"),
          credential: registrationJSON(credential)
        })
      });
      if (!verified.ok) throw new Error("The passkey could not be verified.");
      location.href = "/settings#passkeys";
    } catch (error) {
      if (status) status.textContent = error.message || "Passkey registration was cancelled.";
    }
  });

  const login = document.getElementById("passkey-login");
  login?.addEventListener("click", async () => {
    const status = document.querySelector("[data-passkey-login-status]");
    try {
      const response = await fetch("/login/passkey/options", { method: "POST" });
      if (!response.ok) throw new Error();
      const options = await response.json();
      const challengeId = options.challenge_id;
      delete options.challenge_id;
      const credential = await navigator.credentials.get({ publicKey: prepareRequest(options) });
      const verified = await fetch("/login/passkey/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ challenge_id: challengeId, credential: authenticationJSON(credential) })
      });
      if (!verified.ok) throw new Error();
      location.href = login.dataset.next || "/month";
    } catch (_) {
      if (status) status.textContent = "Passkey sign-in was cancelled or could not be verified.";
    }
  });
})();
