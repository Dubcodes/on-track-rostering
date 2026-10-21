(() => {
  const dialog = document.querySelector("[data-map-dialog]");
  if (!dialog) return;
  const viewport = dialog.querySelector("[data-map-viewport]");
  const image = dialog.querySelector("[data-map-image]");
  let scale = 1;
  const apply = () => { if (image) image.style.width = `${Math.round(image.naturalWidth * scale)}px`; };
  const fit = () => {
    if (!image?.naturalWidth || !viewport) return;
    scale = Math.min(1, (viewport.clientWidth - 24) / image.naturalWidth, (viewport.clientHeight - 24) / image.naturalHeight);
    apply();
    viewport.scrollTo(0, 0);
  };
  document.querySelector("[data-map-open]")?.addEventListener("click", () => { dialog.showModal(); requestAnimationFrame(fit); });
  dialog.querySelector("[data-map-close]")?.addEventListener("click", () => dialog.close());
  dialog.querySelector("[data-map-zoom-in]")?.addEventListener("click", () => { scale = Math.min(4, scale * 1.25); apply(); });
  dialog.querySelector("[data-map-zoom-out]")?.addEventListener("click", () => { scale = Math.max(.2, scale / 1.25); apply(); });
  dialog.querySelector("[data-map-reset]")?.addEventListener("click", fit);
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
})();
