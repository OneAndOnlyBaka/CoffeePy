/* The language button without loading the page again (kiosk.views.language).
 *
 * The button swaps #panel like a nav button; its answer carries, in the HX-Trigger header, every
 * text the page shows outside the panel (kiosk.views._page_texts), and this puts them in place.
 * A page load per tap was a navigation, and every navigation risks Chromium's renderer crash on
 * the Pi (CLAUDE.md) - tapping the button a few times in a row crashed it (2026-11-01).
 *
 * Only text changes. Measured on the Pi (2026-11-01, 8 switches each): setting <html lang> crashed
 * the renderer 7 times, the nav icons' alt 2 times, text alone never - both rebuild layout, and
 * that is where Chromium falls over. So the page keeps the lang it was loaded with, and the
 * icons have an empty alt (their label is right below them).
 *
 * A second tap within PAUSE_MS is dropped: each one swaps the panel, and a swap of a differing
 * panel is the same teardown, only rarer. */
(function () {
  "use strict";

  const PAUSE_MS = 1500;
  let lastTap = 0;

  // Capturing on the document runs before htmx's own listener on the button.
  document.addEventListener("click", (evt) => {
    if (!evt.target.closest(".lang-btn")) return;
    const now = Date.now();
    if (now - lastTap < PAUSE_MS) {
      evt.preventDefault();
      evt.stopPropagation();
      return;
    }
    lastTap = now;
  }, true);

  document.body.addEventListener("kiosk-language", (evt) => {
    const { texts } = evt.detail;
    document.title = texts.title;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = texts[el.dataset.i18n]; });
    const osk = document.getElementById("osk-modal");
    if (osk) {
      osk.dataset.spaceLabel = texts.space;
      osk.querySelectorAll(".osk-space").forEach((key) => { key.textContent = texts.space; });
    }
    document.getElementById("maintenance-steps").textContent = JSON.stringify(texts.steps);
  });
})();
