/* #panel polls every ~500ms via htmx (see index.html's "kiosk-poll" trigger below) so a
 * new card tap is picked up in near-real-time. That's fine for short static states, but
 * a long scrollable list (e.g. many coffee sorts in .panel-body) would otherwise get
 * its whole subtree replaced mid-swipe every poll - the browser loses the touch/scroll
 * gesture it was tracking (the element it started on no longer exists) and the new
 * element always starts at scrollTop 0, which looks like "can't scroll" / "snaps back
 * to the top". Fix: pause polling while there's active scroll/touch activity inside
 * .panel-body, and restore scroll position across any swap that still lands mid-gesture
 * (e.g. residual momentum scrolling) as a safety net. */
(function () {
  "use strict";

  const RESUME_DELAY_MS = 700;
  let pausedUntil = 0;

  function pause() {
    pausedUntil = Date.now() + RESUME_DELAY_MS;
  }

  // capture:true so this still sees scroll events from a .panel-body element that gets
  // destroyed and recreated on every swap - scroll doesn't bubble, but capture-phase
  // listeners on document see it on the way down regardless of which element it's on.
  document.addEventListener(
    "scroll",
    (e) => {
      if (e.target.classList && e.target.classList.contains("panel-body")) pause();
    },
    true
  );

  document.addEventListener(
    "touchstart",
    (e) => {
      if (e.target.closest && e.target.closest(".panel-body")) pause();
    },
    { passive: true }
  );

  document.body.addEventListener("htmx:beforeSwap", (evt) => {
    if (evt.target.id !== "panel") return;
    const body = evt.target.querySelector(".panel-body");
    evt.target.dataset.savedScrollTop = body ? body.scrollTop : 0;
  });

  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.target.id !== "panel") return;
    const body = evt.target.querySelector(".panel-body");
    if (body) body.scrollTop = Number(evt.target.dataset.savedScrollTop || 0);
  });

  setInterval(() => {
    if (Date.now() < pausedUntil) return;
    const panel = document.getElementById("panel");
    if (panel) panel.dispatchEvent(new Event("kiosk-poll"));
  }, 500);
})();
