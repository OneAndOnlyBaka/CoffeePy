/* Easter egg: tapping the nav bar in the sequence Ranking, Ranking, Order, Settings,
 * Ranking within 10 seconds opens Doom. Each nav tap is a real link -> full page load
 * (see kiosk/urls.py's nav() view), so the running tally has to survive page reloads;
 * sessionStorage does that without needing any server-side state for what's just a toy. */
(function () {
  "use strict";

  const SEQUENCE = ["ranking", "ranking", "order", "settings", "ranking"];
  const WINDOW_MS = 10000;
  const STORAGE_KEY = "kioskSecretTaps";

  const frame = document.querySelector(".kiosk-frame");
  if (!frame) return;
  const mode = frame.dataset.mode;
  const doomUrl = frame.dataset.doomUrl;
  if (!mode || !doomUrl) return;

  const now = Date.now();
  let taps = [];
  try {
    taps = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
  } catch (e) {
    taps = [];
  }
  taps = taps.filter((t) => now - t.time < WINDOW_MS);
  taps.push({ mode: mode, time: now });
  taps = taps.slice(-SEQUENCE.length);

  const matches = taps.length === SEQUENCE.length && taps.every((t, i) => t.mode === SEQUENCE[i]);
  if (matches) {
    sessionStorage.removeItem(STORAGE_KEY);
    window.location.href = doomUrl;
    return;
  }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(taps));
})();
