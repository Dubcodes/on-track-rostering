(() => {
  const dialog = document.querySelector("[data-map-dialog]");
  if (!dialog) return;
  document.querySelector("[data-map-open]")?.addEventListener("click", () => dialog.showModal());
  dialog.querySelector("[data-map-close]")?.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
})();
