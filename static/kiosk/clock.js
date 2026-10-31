/* The wait screen's clock, ticking in the browser.
 *
 * It used to be rendered by the server ({% now %}), which made the panel's HTML differ on
 * every other poll - so panel_stable_swap.js could never skip the swap, and the screen's
 * animation restarted at frame 0 every second. The kiosk's browser runs on the same box
 * as the app, so its clock is the server's clock anyway. */
(function () {
  "use strict";

  const pad = (n) => String(n).padStart(2, "0");

  function render() {
    const now = new Date();
    const time = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    const date = `${pad(now.getDate())}.${pad(now.getMonth() + 1)}.${now.getFullYear()}`;
    document.querySelectorAll("[data-kiosk-clock]").forEach((el) => {
      // Built once per clock, then only its text changes: replacing the span every second tore
      // its layout down - the teardown Chromium's renderer crashes in (keyboard_page.js).
      if (!el.firstChild) el.append(document.createTextNode(""), document.createElement("span"));
      el.lastChild.className = "date";
      el.firstChild.nodeValue = `${time} `;
      el.lastChild.textContent = date;
    });
  }

  render();
  setInterval(render, 1000);
  // A freshly swapped-in screen would otherwise show an empty clock for up to a second.
  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.target.id === "panel") render();
  });
})();
