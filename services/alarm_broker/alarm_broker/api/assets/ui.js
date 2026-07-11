(() => {
  "use strict";
  let opener = null;
  let formDirty = false;

  const adminNavigation = document.querySelector(".admin-nav");
  if (adminNavigation instanceof HTMLDetailsElement && window.matchMedia("(max-width: 48rem)").matches) {
    adminNavigation.removeAttribute("open");
  }

  document.addEventListener("input", (event) => {
    if (event.target.closest("form")) formDirty = true;
  });
  document.addEventListener("submit", (event) => {
    formDirty = false;
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
  });
  document.addEventListener("click", (event) => {
    const openTrigger = event.target.closest("[data-dialog-open]");
    if (openTrigger) {
      const dialog = document.getElementById(openTrigger.dataset.dialogOpen);
      if (dialog instanceof HTMLDialogElement) {
        opener = openTrigger;
        dialog.showModal();
      }
      return;
    }
    const closeTrigger = event.target.closest("[data-dialog-close]");
    if (closeTrigger) closeTrigger.closest("dialog")?.close();
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("close", () => {
      if (opener instanceof HTMLElement) opener.focus();
      opener = null;
    });
  });
  document.querySelectorAll("time[datetime]").forEach((element) => {
    const value = new Date(element.dateTime);
    if (!Number.isNaN(value.valueOf())) element.textContent = value.toLocaleString();
  });

  const notice = document.querySelector("[data-poll-url]");
  if (!notice || !notice.dataset.pollUrl || !window.fetch) return;
  const interval = Number(notice.dataset.pollInterval) || 0;
  if (interval < 5) return;
  const initialRevision = notice.dataset.revision;
  const refresh = () => {
    if (document.hidden || formDirty || document.querySelector("dialog[open]")) return;
    window.fetch(notice.dataset.pollUrl, {
      headers: { "X-Requested-With": "AlarmBrokerUI" },
      credentials: "same-origin",
    }).then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (payload && payload.revision && payload.revision !== initialRevision) {
          notice.hidden = false;
        }
      }).catch(() => {});
  };
  window.setInterval(refresh, interval * 1000);
  notice.querySelector("[data-poll-refresh]")?.addEventListener("click", () => {
    window.location.reload();
  });
})();
