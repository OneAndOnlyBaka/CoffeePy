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

  // Typing a name or picking a coffee on Settings gives the member more time (views.settings_touch).
  // The server keeps rendering the deadline it showed - a new one would swap the panel mid-edit -
  // so the visible countdown is moved on here, by the same amount, once the server has it.
  document.body.addEventListener("htmx:afterRequest", (evt) => {
    const path = evt.detail.requestConfig && evt.detail.requestConfig.path;
    if (!evt.detail.successful || !path) return;
    // A pick on Order (views.order_select) is the same, and turns the automatic checkout off.
    const picked = path.endsWith("/order/select/");
    if (!picked && !path.endsWith("/settings/touch/")) return;
    document.querySelectorAll(".countdown[data-touch-seconds]").forEach((el) => {
      el.dataset.deadline = Date.now() + Number(el.dataset.touchSeconds) * 1000;
      if (picked && el.dataset.pickedText) el.dataset.text = el.dataset.pickedText;
    });
    render();
  });

  render();
  setInterval(render, 250);
  // A freshly swapped-in screen would otherwise show an empty countdown for a moment.
  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.target.id === "panel") render();
  });
})();
