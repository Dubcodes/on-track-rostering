(() => {
  const form = document.querySelector("[data-onboarding-form]");
  if (!form) return;

  const changedRows = new Set();
  const count = form.querySelector("[data-onboarding-count]");
  const submit = form.querySelector("[data-onboarding-submit]");

  form.querySelectorAll("[data-onboarding-row]").forEach((row) => {
    row.querySelectorAll("[data-onboarding-control]").forEach((control) => {
      const initial = control.value;
      control.disabled = false;
      control.addEventListener("input", () => {
        const changed = [...row.querySelectorAll("[data-onboarding-control]")]
          .some((candidate) => candidate.value !== candidate.dataset.initialValue);
        row.querySelector("[data-onboarding-value]").disabled = !changed;
        if (changed) changedRows.add(row); else changedRows.delete(row);
        count.textContent = String(changedRows.size);
        submit.disabled = changedRows.size === 0;
      });
      control.dataset.initialValue = initial;
    });
  });

  form.addEventListener("submit", () => {
    form.querySelectorAll("[data-onboarding-row]").forEach((row) => {
      if (!changedRows.has(row)) {
        row.querySelectorAll("[data-onboarding-control]").forEach((control) => {
          control.disabled = true;
        });
      }
    });
  });
})();
