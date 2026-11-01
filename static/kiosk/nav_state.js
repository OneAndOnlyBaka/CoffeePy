/* Keeps the nav bar's highlighted button in step with the screen.
 *
 * The nav buttons no longer load a page (see views.nav), so nothing re-renders the bar
 * itself. The tapped button lights up right away - on a Pi 3 the panel takes a moment, and
 * a tap without feedback gets tapped again. After that the panel is the authority: it
 * reports its mode on #panel-state, which also catches the server switching modes on its
 * own (an unknown card is sent to the settings screen) - and whether there is a ranking or a
 * purchase screen at all, which the admin can switch off from the dashboard while the kiosk runs. */
(function () {
  "use strict";

  const frame = document.querySelector(".kiosk-frame");
  const buttons = document.querySelectorAll(".nav-btn[data-mode]");
  if (!frame || !buttons.length) return;

  function highlight(mode) {
    if (!mode) return;
    frame.dataset.mode = mode;
    buttons.forEach((btn) => btn.classList.toggle("active", btn.dataset.mode === mode));
  }

  buttons.forEach((btn) => btn.addEventListener("click", () => highlight(btn.dataset.mode)));

  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.target.id !== "panel") return;
    const marker = document.getElementById("panel-state");
    if (!marker) return;
    highlight(marker.dataset.mode);
    const ranking = document.querySelector('.nav-btn[data-mode="ranking"]');
    if (ranking) ranking.hidden = marker.dataset.ranking !== "1";
    const purchase = document.querySelector('.nav-btn[data-mode="purchase"]');
    if (purchase) purchase.hidden = marker.dataset.inventory !== "1";
  });
})();
