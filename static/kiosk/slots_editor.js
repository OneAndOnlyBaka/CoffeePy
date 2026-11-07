/* The editor of "my coffees" (panel.html state "slots", coffee/slots.py).
 *
 * Every control is in the panel from the start; a tap only changes a hidden field and a text, and a
 * row the slot's sort can't set is made invisible (visibility, not display) - nothing is built or torn
 * down, the panel stays the same bytes from poll to poll until Save (CLAUDE.md, the renderer crash).
 * The limits come from the machine's recipe of each sort (#slot-recipes); the server clamps again.
 * Each change also tells the server someone is busy here (views.settings_touch), at most every few
 * seconds - countdown.js moves the visible logout on. */
(function () {
  "use strict";

  let lastTouch = 0;

  function recipes() {
    const el = document.getElementById("slot-recipes");
    return el ? JSON.parse(el.textContent) : {};
  }

  function touch(form) {
    if (Date.now() - lastTouch < 3000 || !window.htmx) return;
    lastTouch = Date.now();
    htmx.ajax("POST", form.dataset.touchUrl, { source: form, swap: "none" });
  }

  function field(card, name) {
    return card.querySelector(`input[name="s${card.dataset.pos}-${name}"]`);
  }

  function render(form, card) {
    const sortId = field(card, "sort").value;
    const entry = recipes()[sortId];
    const recipe = (entry && entry.recipe) || {};
    const show = (name, text) => { card.querySelector(`[data-show="${name}"]`).textContent = text; };
    show("sort", entry ? entry.name : form.dataset.empty);
    show("strength", field(card, "strength").value);
    show("water", field(card, "water").value ? `${field(card, "water").value} ml` : "");
    show("temperature", form.getAttribute(`data-temp-${field(card, "temperature").value}`) || "");
    show("count", `${field(card, "count").value} ×`);
    card.querySelectorAll("[data-needs]").forEach((row) => {
      const need = row.dataset.needs;
      const on = need === "sort" ? Boolean(entry) : Boolean(entry && recipe[need]);
      row.style.visibility = on ? "visible" : "hidden";
    });
    card.classList.toggle("empty", !entry);
  }

  function reset(card, recipe) {
    field(card, "strength").value = recipe.strength ? recipe.strength.default : "";
    field(card, "water").value = recipe.water ? recipe.water.default : "";
    field(card, "temperature").value = recipe.temperature ? recipe.temperature.default : "";
  }

  function step(form, card, what, by) {
    const sortId = field(card, "sort").value;
    const entry = recipes()[sortId];
    const recipe = (entry && entry.recipe) || {};
    if (what === "sort") {
      const ids = form.dataset.sortIds.split(",");
      const next = ids[(ids.indexOf(sortId) + by + ids.length) % ids.length];
      field(card, "sort").value = next;
      reset(card, ((recipes()[next] || {}).recipe) || {});
    } else if (what === "strength" && recipe.strength) {
      const now = Number(field(card, "strength").value || recipe.strength.default);
      field(card, "strength").value = Math.max(recipe.strength.min, Math.min(recipe.strength.max, now + by));
    } else if (what === "water" && recipe.water) {
      // 10 ml a tap: 5 ml steps make a 25-240 ml range 43 taps long.
      const w = recipe.water;
      const now = Number(field(card, "water").value || w.default);
      const stepMl = Math.max(w.step, 10);
      let next = Math.round((now + by * stepMl - w.min) / w.step) * w.step + w.min;
      field(card, "water").value = Math.max(w.min, Math.min(w.max, next));
    } else if (what === "temperature" && recipe.temperature) {
      const options = recipe.temperature.options;
      const now = Number(field(card, "temperature").value || recipe.temperature.default);
      field(card, "temperature").value = options[(options.indexOf(now) + 1) % options.length];
    } else if (what === "count") {
      const now = Number(field(card, "count").value || 1);
      field(card, "count").value = Math.max(1, Math.min(Number(form.dataset.maxCount), now + by));
    }
    render(form, card);
    touch(form);
  }

  function setup(root) {
    const form = root.querySelector && root.querySelector(".slots-editor");
    if (!form) return;
    form.querySelectorAll(".slot-card").forEach((card) => render(form, card));
  }

  document.addEventListener("click", (e) => {
    const button = e.target.closest && e.target.closest(".slots-editor [data-step]");
    if (!button) return;
    const form = button.closest(".slots-editor");
    step(form, button.closest(".slot-card"), button.dataset.step, Number(button.dataset.by));
  });
  document.addEventListener("change", (e) => {
    const form = e.target.closest && e.target.closest(".slots-editor");
    if (form) touch(form);
  });
  // The editor arrives with a swap of #panel; a poll that changes nothing doesn't swap it again.
  document.body.addEventListener("htmx:afterSwap", (evt) => {
    if (evt.target.id === "panel") setup(evt.target);
  });
  setup(document);
})();
