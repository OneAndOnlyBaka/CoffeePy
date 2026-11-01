/* Turns the pages of sort tiles (panel.html, only with more sorts than fit on one screen).
 *
 * Every page is in the panel from the start, stacked in one grid cell; this only moves the
 * "current" class, so the others are merely invisible (kiosk.css). Nothing is built or torn
 * down and nothing scrolls - a swipe through a long list and then leaving the screen crashed
 * Chromium's renderer on the device (CLAUDE.md). The button's own hx-post tells the server
 * that someone is choosing: on Order that ends the automatic checkout, as a pick does. */
(function () {
  "use strict";

  document.addEventListener("click", (e) => {
    const button = e.target.closest && e.target.closest("[data-sort-page]");
    if (!button) return;
    const pages = button.closest(".sort-pages");
    if (!pages) return;
    pages.querySelectorAll(":scope > .sort-page").forEach((page) => {
      page.classList.toggle("current", page.dataset.page === button.dataset.sortPage);
    });
  });
})();
