/* Skips the panel swap when a poll returns exactly what is already on screen.
 *
 * #panel is re-fetched every ~500ms (see panel_scroll.js) so a card tap is picked up in
 * near-real-time, and htmx replaces the subtree every single time - even when the HTML
 * is byte-for-byte identical, which it is for every static screen. That is what kept the
 * "thank you" gifs from ever playing: an <img> that is rebuilt (or merely detached and
 * re-attached, which is what hx-preserve does when it moves the live node into the new
 * content) restarts its animation at frame 0, so nothing past the first half second was
 * ever visible. Dropping the redundant swap leaves the element - and its running gif -
 * completely untouched.
 *
 * A poll that actually carries news (another member's card, the interlude ending, a new
 * balance) returns different HTML and swaps immediately, so nothing gets slower. */
(function () {
  "use strict";

  let lastResponse = null;

  document.body.addEventListener("htmx:beforeSwap", (evt) => {
    if (evt.target.id !== "panel") return;
    const response = evt.detail.serverResponse;
    if (response === lastResponse) {
      evt.detail.shouldSwap = false;
      return;
    }
    lastResponse = response;
  });
})();
