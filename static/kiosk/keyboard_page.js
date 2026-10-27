/* On-screen keyboard for the standalone /kiosk/keyboard/ page. Unlike the main kiosk
 * panel, this page is never re-rendered/polled while the user is on it, so there's no
 * focus-preservation problem to work around - this is intentionally much simpler than
 * an embedded widget would need to be. */
(function () {
  "use strict";

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

  const input = document.getElementById("keyboard-input");
  const keyboardEl = document.getElementById("onscreen-keyboard");
  const numeric = keyboardEl.dataset.numeric === "1";
  // Rendered by the template, which knows the page's language - this file is static.
  const spaceLabel = keyboardEl.dataset.spaceLabel || "Space";
  let shift = false;

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

  function insertText(text) {
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.setRangeText(text, start, end, "end");
  }

  function backspace() {
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    if (start === end && start > 0) {
      input.setRangeText("", start - 1, end, "end");
    } else {
      input.setRangeText("", start, end, "end");
    }
  }

  function render() {
    keyboardEl.innerHTML = "";
    const rows = numeric ? NUMERIC_ROWS : TEXT_ROWS;
    rows.forEach((row) => {
      const rowEl = document.createElement("div");
      rowEl.className = "osk-row";
      row.forEach((key) => {
        // "ß".toUpperCase() is "SS" - that one stays as it is.
        const upper = key.toUpperCase().length === 1 ? key.toUpperCase() : key;
        const label = !numeric && shift ? upper : key;
        rowEl.appendChild(makeKey(label, () => insertText(shift ? upper : key)));
      });
      keyboardEl.appendChild(rowEl);
    });

    const bottomRow = document.createElement("div");
    bottomRow.className = "osk-row";
    if (!numeric) {
      bottomRow.appendChild(
        makeKey(
          "⇧",
          () => {
            shift = !shift;
            render();
          },
          "osk-shift" + (shift ? " active" : "")
        )
      );
      bottomRow.appendChild(makeKey(spaceLabel, () => insertText(" "), "osk-space"));
    }
    bottomRow.appendChild(makeKey("⌫", backspace, "osk-backspace"));
    keyboardEl.appendChild(bottomRow);
  }

  render();
  input.focus();
  if (numeric) input.select();
  // A suggested name ("Anna ") is meant to be added to, not typed in front of.
  else input.setSelectionRange(input.value.length, input.value.length);
})();
