(() => {
  const slug = (value) => value.normalize("NFKD").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  document.querySelectorAll('form[action^="/admin/online-sources/"]').forEach((form) => {
    const provider = form.querySelector('[name="provider"]')?.value;
    const source = form.querySelector('[name="external_track_name"]')?.value;
    const item = form.closest(".control-record");
    if (!provider || !source || !item) return;
    item.id ||= `source-${slug(`${provider}-${source}`)}`;
    form.addEventListener("submit", () => {
      let returnTo = form.querySelector('[name="return_to"]');
      if (!returnTo) {
        returnTo = document.createElement("input");
        returnTo.type = "hidden";
        returnTo.name = "return_to";
        form.append(returnTo);
      }
      returnTo.value = item.id;
    });
  });
  document.querySelectorAll("section.panel").forEach((section) => {
    if (section.querySelector("h2")?.textContent.trim() === "Unmatched source identities") {
      section.id ||= "unmatched-sources";
    }
    if (section.querySelector("h2")?.textContent.trim() !== "Confirmed source mappings") return;
    section.querySelectorAll(".record-list > span").forEach((item) => {
      const identity = item.querySelector("strong")?.textContent.trim();
      if (identity) item.id = `source-${slug(identity)}`;
    });
  });
  if (!window.location.hash) return;
  const target = document.getElementById(window.location.hash.slice(1));
  if (!target) return;
  if (target instanceof HTMLDetailsElement) target.open = true;
  target.closest("details")?.setAttribute("open", "");
  window.requestAnimationFrame(() => target.scrollIntoView({block: "center"}));
})();
