/* Easter egg: tapping the nav bar in the sequence Ranking, Ranking, Order, Settings,
 * Ranking within 10 seconds opens Doom. The nav buttons swap the panel in place (see
 * views.nav), so the taps are counted as they happen; sessionStorage still keeps the
 * tally across a reload, without needing any server-side state for what's just a toy. */
(function () {
  "use strict";

  const SEQUENCE = ["ranking", "ranking", "order", "settings", "ranking"];
  const WINDOW_MS = 10000;
  const STORAGE_KEY = "kioskSecretTaps";

  const frame = document.querySelector(".kiosk-frame");
  if (!frame) return;
  const doomUrl = frame.dataset.doomUrl;
  if (!doomUrl) return;

  function load() {
    try {
      return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function record(mode) {
    const now = Date.now();
    let taps = load().filter((t) => now - t.time < WINDOW_MS);
    taps.push({ mode: mode, time: now });
    taps = taps.slice(-SEQUENCE.length);

    const matches = taps.length === SEQUENCE.length && taps.every((t, i) => t.mode === SEQUENCE[i]);
    if (matches) {
      sessionStorage.removeItem(STORAGE_KEY);
      window.location.href = doomUrl;
      return;
    }
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(taps));
    } catch (e) {
      // No storage, no easter egg - the kiosk itself must not care.
    }
  }

  document.querySelectorAll(".nav-btn[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => record(btn.dataset.mode));
  });
})();
