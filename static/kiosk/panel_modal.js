/* Closes the QR modal once the panel underneath it has moved on.
 *
 * The modal deliberately lives outside #panel (see index.html) so the 500ms poll can't
 * wipe it away while someone is still scanning it. The flip side: nothing takes it away
 * when the panel changes on its own. That matters most for "Link this phone" - the code
 * in it is a one-time login token for that member's account, and the settings screen logs
 * itself out after ~20s idle, so the token would stay on screen for whoever walks up next.
 *
 * The panel reports who it currently belongs to as "<state>:<uid>" (see _render_panel);
 * any change to that - logged out, different card, different screen - closes the modal.
 * Repeated polls of the same screen don't, so a slow scan is never interrupted. */
(function () {
  "use strict";

  const modal = document.getElementById("qr-modal");
  if (!modal) return;

  function panelKey() {
    const marker = document.getElementById("panel-state");
    return marker ? marker.dataset.panelKey : null;
  }

  let lastKey = panelKey();

  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.target.id !== "panel") return;
    const key = panelKey();
    // lastKey === null on the very first swap (the panel starts as a "Loading…"
    // placeholder with no marker) - that's not the panel moving on.
    if (lastKey !== null && key !== lastKey) {
      modal.classList.remove("open");
      document.dispatchEvent(new Event("osk:close"));  // the keyboard overlay (osk.js) too
    }
    lastKey = key;
  });
})();
