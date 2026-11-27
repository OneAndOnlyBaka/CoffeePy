/* The language button without loading the page again (kiosk.views.language).
 *
 * On the wait screen - where nearly every switch happens - both languages are already on the page,
 * stacked (panel.html): a tap only moves .kiosk-frame's data-lang, which shows the other copy,
 * relabels the rest of the page from the texts index.html carries for every language, and tells the
 * server in the background (?to=, 204). Nothing is fetched first and nothing swapped: no wait, no
 * flicker, and the wait screen's animation runs on (a swap restarted it at frame 0).
 *
 * Elsewhere the button swaps #panel like a nav button; its answer carries, in the HX-Trigger
 * header, every text the page shows outside the panel (kiosk.views._page_texts), and this puts
 * them in place. A page load per tap was a navigation, and every navigation risks Chromium's
 * renderer crash on the Pi (CLAUDE.md) - tapping the button a few times in a row crashed it
 * (2026-11-01).
 *
 * Only text changes. Measured on the Pi (2026-11-01, 8 switches each): setting <html lang> crashed
 * the renderer 7 times, the nav icons' alt 2 times, text alone never - both rebuild layout, and
 * that is where Chromium falls over. So the page keeps the lang it was loaded with, and the
 * icons have an empty alt (their label is right below them).
 *
 * A second tap of the swapping button within PAUSE_MS is dropped: each one swaps the panel, and a
 * swap of a differing panel is the same teardown, only rarer. The wait screen's needs no pause. */
(function () {
  "use strict";

  const PAUSE_MS = 1500;
  let lastTap = 0;
  const frame = document.querySelector(".kiosk-frame");
  const allTexts = JSON.parse(document.getElementById("kiosk-texts").textContent);
  // One save after the other, in the order of the taps: the last tap's language is the one kept.
  let saving = Promise.resolve();

  function relabel(texts) {
    document.title = texts.title;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = texts[el.dataset.i18n]; });
    const osk = document.getElementById("osk-modal");
    if (osk) {
      osk.dataset.spaceLabel = texts.space;
      osk.querySelectorAll(".osk-space").forEach((key) => { key.textContent = texts.space; });
    }
    document.getElementById("maintenance-steps").textContent = JSON.stringify(texts.steps);
  }

  function show(lang, href) {
    frame.dataset.lang = lang;
    relabel(allTexts[lang]);
    const url = `${href}?to=${encodeURIComponent(lang)}`;
    saving = saving.then(() => fetch(url, { credentials: "same-origin", cache: "no-store" })).catch(() => {});
  }

  // Capturing on the document runs before htmx's own listener on the button.
  document.addEventListener("click", (evt) => {
    const button = evt.target.closest(".lang-btn");
    if (!button) return;
    if (button.dataset.langTo) {
      evt.preventDefault();
      evt.stopPropagation();
      show(button.dataset.langTo, button.getAttribute("href"));
      return;
    }
    const now = Date.now();
    if (now - lastTap < PAUSE_MS) {
      evt.preventDefault();
      evt.stopPropagation();
      return;
    }
    lastTap = now;
  }, true);

  document.body.addEventListener("kiosk-language", (evt) => {
    frame.dataset.lang = evt.detail.lang;
    relabel(evt.detail.texts);
  });
})();
