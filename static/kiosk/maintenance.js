/* "We'll be right back" while the device is being worked on.
 *
 * install.sh and the update watcher write their progress to /maintenance/status.json, which
 * Caddy serves from disk (install/maintenance.sh) - so this still gets an answer while the
 * app is restarting. A status shows the overlay with its progress; so does the panel's own
 * poll failing for a few seconds in a row, which catches a restart nothing announced. Once
 * it is over, the page reloads: an update may have changed the kiosk itself.
 *
 * The overlay sits outside #panel, above everything, so it also takes the taps. */
(function () {
  "use strict";

  const overlay = document.getElementById("maintenance");
  if (!overlay) return;
  const steps = JSON.parse(document.getElementById("maintenance-steps").textContent);
  const bar = overlay.querySelector(".maintenance-bar");
  const fill = bar.querySelector("span");
  const stepText = overlay.querySelector(".maintenance-step");

  const STALE_SECONDS = 2 * 3600;  // a status left behind by a process that died
  const FAILED_POLLS = 6;          // ~3s of the 500ms panel poll not getting through
  let active = false;
  let failures = 0;

  function show(status) {
    active = true;
    overlay.hidden = false;
    bar.style.visibility = status ? "visible" : "hidden";
    if (status) fill.style.width = Math.max(0, Math.min(100, status.percent)) + "%";
    stepText.textContent = (status && steps[status.step]) || "";
  }

  async function fetchStatus() {
    try {
      const response = await fetch(overlay.dataset.statusUrl, { cache: "no-store" });
      if (!response.ok) return null;
      const status = await response.json();
      return Date.now() / 1000 - status.updated < STALE_SECONDS ? status : null;
    } catch (e) {
      return null;
    }
  }

  async function healthy() {
    try {
      return (await fetch(overlay.dataset.healthUrl, { cache: "no-store" })).ok;
    } catch (e) {
      return false;
    }
  }

  async function check() {
    const status = await fetchStatus();
    if (status) {
      show(status);
    } else if (active && (await healthy())) {
      location.reload();
      return;
    }
    setTimeout(check, 2000);
  }

  document.body.addEventListener("htmx:afterRequest", (evt) => {
    if (evt.target.id !== "panel") return;
    failures = evt.detail.successful ? 0 : failures + 1;
    if (failures >= FAILED_POLLS && !active) show(null);
  });

  check();
})();
