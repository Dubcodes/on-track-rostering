(() => {
  const template = document.querySelector("[data-equipment-editor-row]");
  if (!template) return;
  document.addEventListener("click", (event) => {
    const add = event.target.closest("[data-add-equipment]");
    if (add) {
      add.closest("[data-equipment-editor]").querySelector("[data-equipment-rows]")
        .append(template.content.cloneNode(true));
      return;
    }
    const remove = event.target.closest("[data-remove-equipment]");
    if (remove) remove.closest(".equipment-row").remove();
  });
})();
