/* Loads shareware DOOM (js-dos/DOSBox in WASM) into the standalone /kiosk/doom/ page and
 * wires the touch D-pad to synthetic keyboard events, since DOSBox reads real
 * keydown/keyup events off window/document the same way it would from a physical
 * keyboard - there's no separate touch API to hook into. */
(function () {
  "use strict";

  const KEYS = {
    ArrowUp: { code: "ArrowUp", key: "ArrowUp", keyCode: 38 },
    ArrowDown: { code: "ArrowDown", key: "ArrowDown", keyCode: 40 },
    ArrowLeft: { code: "ArrowLeft", key: "ArrowLeft", keyCode: 37 },
    ArrowRight: { code: "ArrowRight", key: "ArrowRight", keyCode: 39 },
    Control: { code: "ControlLeft", key: "Control", keyCode: 17 },
    Space: { code: "Space", key: " ", keyCode: 32 },
    Enter: { code: "Enter", key: "Enter", keyCode: 13 },
    Escape: { code: "Escape", key: "Escape", keyCode: 27 },
  };

  function sendKey(type, name) {
    const spec = KEYS[name];
    if (!spec) return;
    const event = new KeyboardEvent(type, {
      code: spec.code,
      key: spec.key,
      keyCode: spec.keyCode,
      which: spec.keyCode,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(event);
    document.dispatchEvent(event);
  }

  function wireDpad() {
    document.querySelectorAll("[data-key]").forEach((btn) => {
      const name = btn.dataset.key;
      const press = (e) => {
        e.preventDefault();
        sendKey("keydown", name);
      };
      const release = (e) => {
        e.preventDefault();
        sendKey("keyup", name);
      };
      // pointerdown/up (not click) so movement starts/stops exactly while held, and
      // pointerleave/cancel releases the key if a finger slides off the button.
      btn.addEventListener("pointerdown", press);
      btn.addEventListener("pointerup", release);
      btn.addEventListener("pointerleave", release);
      btn.addEventListener("pointercancel", release);
    });
  }

  async function loadBytes(url) {
    const res = await fetch(url);
    return new Uint8Array(await res.arrayBuffer());
  }

  async function start() {
    const container = document.getElementById("dos");
    const [exe, wad] = await Promise.all([
      loadBytes(container.dataset.exe),
      loadBytes(container.dataset.wad),
    ]);

    // EXPERIMENT ONLY (performance measurements) - to be removed.
    const q = new URLSearchParams(location.search);
    let conf = "";
    if (q.get("cycles")) conf += "[cpu]\ncycles=" + q.get("cycles") + "\n";
    if (q.get("sound") === "0") conf += "[mixer]\nnosound=true\n[sblaster]\nsbtype=none\n[speaker]\npcspeaker=false\ntandy=off\n";
    if (q.get("frameskip")) conf += "[render]\nframeskip=" + q.get("frameskip") + "\n";
    window.__frames = 0;
    const cfg = [];
    if (q.get("detail")) cfg.push("detaillevel " + q.get("detail"));
    if (q.get("blocks")) cfg.push("screenblocks " + q.get("blocks"));
    if (q.get("nosfx")) cfg.push("snd_sfxdevice 0", "snd_musicdevice 0");
    Dos(container, {
      pathPrefix: container.dataset.emulatorsPrefix,
      initFs: [
        { path: "DOOM.EXE", contents: exe },
        { path: "DOOM1.WAD", contents: wad },
      ].concat(cfg.length ? [{ path: "DEFAULT.CFG", contents: new TextEncoder().encode(cfg.join("\r\n") + "\r\n") }] : []),
      dosboxConf: conf + "[autoexec]\nmount c .\nc:\nDOOM.EXE\n",
      autoStart: true,
      noCursor: true,
      renderBackend: q.get("render") || "webgl",
      workerThread: q.get("worker") !== "0",
      onEvent: (event, ci) => {
        if (event === "ci-ready") { window.__ci = ci; ci.events().onFrame(() => { window.__frames++; }); }
      },
    });
  }

  wireDpad();
  start();
})();
