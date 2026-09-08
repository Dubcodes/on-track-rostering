(() => {
  "use strict";
  const enable = document.getElementById("push-enable");
  const disable = document.getElementById("push-disable");
  if (!enable && !disable) return;
  const status = document.querySelector("[data-push-status]");
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || "";
  const decodeKey = (value) => {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4);
    return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
  };
  enable?.addEventListener("click", async () => {
    try {
      const config = await fetch("/settings/notifications/config").then((response) => response.json());
      if (!config.enabled) throw new Error("Push is not configured on this environment.");
      if (await Notification.requestPermission() !== "granted") throw new Error("Notification permission was not granted.");
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: decodeKey(config.public_key)
      });
      const response = await fetch("/settings/notifications/subscription", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({csrf_token: csrf, subscription: subscription.toJSON(), label: navigator.userAgent})
      });
      if (!response.ok) throw new Error("The device subscription could not be saved.");
      if (status) status.textContent = "Notifications are enabled on this device.";
    } catch (error) { if (status) status.textContent = error.message; }
  });
  disable?.addEventListener("click", async () => {
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await fetch("/settings/notifications/subscription/disable", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({csrf_token: csrf, endpoint: subscription.endpoint})
        });
        await subscription.unsubscribe();
      }
      if (status) status.textContent = "Notifications are disabled on this device.";
    } catch (_) { if (status) status.textContent = "Notifications could not be disabled."; }
  });
})();
