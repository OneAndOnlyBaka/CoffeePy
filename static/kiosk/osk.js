/* The kiosk's on-screen keyboard, an overlay on the kiosk page (#osk-modal in index.html).
 *
 * It was a page of its own (/kiosk/keyboard/), and each trip there and back was a navigation -
 * the moment Chromium's renderer crashes now and then while it tears the old page down
 * (CLAUDE.md); on the device it crashed opening the keyboard. Here nothing is navigated, built
 * or torn down while it is used: both key sets exist from the start, the overlay and the set
 * not in use are only made invisible, Shift only relabels the keys.
 *
 * A "Keyboard" button in the panel names its field (data-osk-for, data-osk-numeric for the
 * amount); Done writes the text into that field, which the panel's own form then sends. */
(function () {
  "use strict";

  const modal = document.getElementById("osk-modal");
  if (!modal) return;
  const input = document.getElementById("osk-input");
  const spaceLabel = modal.dataset.spaceLabel || "Space";

  // Umlauts, ß, "-" and "." for names like "Jürgen-Hans Ö." (coffee/names.py).
  const TEXT_ROWS = [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "ü"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l", "ö", "ä"],
    ["z", "x", "c", "v", "b", "n", "m", "ß", "-", "."],
  ];
  const NUMERIC_ROWS = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    [".", "0"],
  ];

  let shift = false;
  let target = null;
  let source = null;
  let lastTouch = 0;
  const letterKeys = [];

  // Typing takes a while: the logout countdown is moved on as on the panel's own fields
  // (views.settings_touch; countdown.js moves the visible one), at most every few seconds.
  function touch() {
    if (!source || Date.now() - lastTouch < 3000 || !window.htmx) return;
    lastTouch = Date.now();
    htmx.ajax("POST", modal.dataset.touchUrl, { source: source, swap: "none" });
  }

  function insertText(text) {
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.setRangeText(text, start, end, "end");
    touch();
  }

  function backspace() {
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.setRangeText("", start === end && start > 0 ? start - 1 : start, end, "end");
    touch();
  }

  function upperOf(key) {
    // "ß".toUpperCase() is "SS" - that one stays as it is.
    return key.toUpperCase().length === 1 ? key.toUpperCase() : key;
  }

  function makeKey(label, onPress, extraClass) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "osk-key" + (extraClass ? " " + extraClass : "");
    btn.textContent = label;
    // pointerdown+preventDefault so tapping a key never blurs the input first.
    btn.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      onPress();
    });
    return btn;
  }

  function build(container, rows, numeric) {
    rows.forEach((row) => {
      const rowEl = document.createElement("div");
      rowEl.className = "osk-row";
      row.forEach((key) => {
        const btn = makeKey(key, () => insertText(!numeric && shift ? upperOf(key) : key));
        if (!numeric) letterKeys.push({ btn, key });
        rowEl.appendChild(btn);
      });
      container.appendChild(rowEl);
    });
    const bottom = document.createElement("div");
    bottom.className = "osk-row";
    if (!numeric) {
      bottom.appendChild(shiftKey);
      bottom.appendChild(makeKey(spaceLabel, () => insertText(" "), "osk-space"));
    }
    bottom.appendChild(makeKey("⌫", backspace, "osk-backspace"));
    container.appendChild(bottom);
  }

  function setShift(on) {
    shift = on;
    letterKeys.forEach(({ btn, key }) => {
      btn.textContent = shift ? upperOf(key) : key;
    });
    shiftKey.classList.toggle("active", shift);
  }

  const shiftKey = makeKey("⇧", () => setShift(!shift), "osk-shift");

  const textKeys = document.getElementById("osk-text");
  const numericKeys = document.getElementById("osk-numeric");
  build(textKeys, TEXT_ROWS, false);
  build(numericKeys, NUMERIC_ROWS, true);

  function open(field, numeric, button) {
    target = field;
    source = button;
    if (shift) setShift(false);  // every time it opens, it opens the same way
    textKeys.classList.toggle("off", numeric);
    numericKeys.classList.toggle("off", !numeric);
    input.inputMode = numeric ? "decimal" : "text";
    input.value = field.value;
    modal.classList.add("open");
    input.focus();
    // A suggested name ("Anna ") is meant to be added to, not typed in front of.
    if (numeric) input.select();
    else input.setSelectionRange(input.value.length, input.value.length);
    lastTouch = 0;
    touch();
  }

  function close() {
    modal.classList.remove("open");
    input.blur();
    target = source = null;
  }

  document.addEventListener("click", (e) => {
    const button = e.target.closest && e.target.closest("[data-osk-for]");
    if (!button) return;
    e.preventDefault();
    const field = document.getElementById(button.dataset.oskFor);
    if (field) open(field, button.hasAttribute("data-osk-numeric"), button);
  });

  document.getElementById("osk-done").addEventListener("click", () => {
    if (target && document.body.contains(target)) {
      target.value = input.value;
      target.dispatchEvent(new Event("input", { bubbles: true }));
    }
    lastTouch = 0;
    touch();
    close();
  });
  document.getElementById("osk-cancel").addEventListener("click", close);

  // The panel moved on underneath (logged out, the next card): the text belongs to nobody now.
  document.addEventListener("osk:close", close);
})();
