/* The "automatic checkout/logout in Ns" countdowns, ticking in the browser.
 *
 * The server renders only the moment it runs out (data-deadline, ms since the epoch) and the
 * text with "{s}" for the seconds - the same on every poll until someone taps, so
 * panel_stable_swap.js skips the swap. Rendered by the server, the seconds made every poll of
 * the order, pay and settings screens differ, and Chromium's renderer crashes now and then while
 * it tears a swapped-out panel down (CLAUDE.md). The browser runs on the app's own box, so its
 * clock is the server's. What happens at zero is still the server's: the next poll books or logs out. */
(function () {
  "use strict";

  function render() {
    const now = Date.now();
    document.querySelectorAll(".countdown[data-deadline]").forEach((el) => {
      const left = Math.max(0, Math.floor((Number(el.dataset.deadline) - now) / 1000));
      const text = el.dataset.text.replace("{s}", left);
      if (el.textContent !== text) el.textContent = text;
    });
  }

  render();
  setInterval(render, 250);
  // A freshly swapped-in screen would otherwise show an empty countdown for a moment.
  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.target.id === "panel") render();
  });
})();
