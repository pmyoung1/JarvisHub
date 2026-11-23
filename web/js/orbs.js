/* Jarvis orbs: a blue "you are speaking" orb and an orange "Jarvis is talking" orb.

   Blue   - AnalyserNode on the microphone stream (getUserMedia). It pulses
            when the RMS level passes SPEECH_THRESHOLD. Needs a secure
            context (HTTPS or localhost) and a mic, so on the TV and on a
            plain-HTTP phone it simply stays idle, or follows hub state (below).
   Orange - AnalyserNode on the talkback <audio> element (createMediaElementSource),
            so it pulses with the actual audio that Piper/SAPI produced.
            Audio comes from POST /api/tts.
   Hub    - SSE "voice" events (state: capturing | processing | speaking) drive
            the same orbs with a synthetic sine pulse, so the hub PC's own
            microphone and speakers light them even when this device has
            no mic or no Web Audio.

   Scale is sine-eased: easeInOutSine(normalized level), then smoothed per frame.
   Plain ES5 on purpose (Tizen browser). No libraries. */
(function (root) {
  "use strict";

  var SPEECH_THRESHOLD = 0.02;   /* RMS (0..1) above which the mic counts as speech */
  var LEVEL_FULL = 0.25;         /* RMS that maps to the largest scale */
  var MAX_SCALE = 1.55;
  var IDLE_BREATH = 0.035;       /* idle scale amplitude */
  var SMOOTH = 0.28;             /* per-frame lerp toward the target */

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function easeInOutSine(t) {
    t = clamp(t, 0, 1);
    return -(Math.cos(Math.PI * t) - 1) / 2;
  }

  /* RMS of an AnalyserNode byte time-domain buffer (128 = silence). */
  function rmsOfBytes(bytes) {
    var sum = 0;
    var n = bytes.length;
    if (!n) return 0;
    for (var i = 0; i < n; i++) {
      var v = (bytes[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / n);
  }

  /* Map a level to a scale: 1 below the speech threshold, sine-eased up to MAX_SCALE. */
  function scaleForLevel(level, threshold) {
    threshold = threshold === undefined ? SPEECH_THRESHOLD : threshold;
    if (level < threshold) return 1;
    var t = (level - threshold) / (LEVEL_FULL - threshold);
    return 1 + (MAX_SCALE - 1) * easeInOutSine(t);
  }

  /* Synthetic pulse (0..1) for states with no analyser. */
  function sinePulse(nowMs, hz) {
    return 0.5 + 0.5 * Math.sin(2 * Math.PI * hz * nowMs / 1000);
  }

  var helpers = {
    easeInOutSine: easeInOutSine,
    rmsOfBytes: rmsOfBytes,
    scaleForLevel: scaleForLevel,
    sinePulse: sinePulse,
    SPEECH_THRESHOLD: SPEECH_THRESHOLD
  };

  if (typeof module !== "undefined" && module.exports) module.exports = helpers; /* node tests */
  if (!root || !root.document) return;

  var doc = root.document;
  var AudioCtx = root.AudioContext || root.webkitAudioContext;

  var state = {
    ctx: null,
    mic: { analyser: null, stream: null, buf: null, active: false },
    out: { analyser: null, buf: null, source: null, hooked: false },
    hubState: "idle",
    talkbackOn: false,
    audioEl: null,
    micEl: null,
    outEl: null,
    micLabel: null,
    outLabel: null,
    micBtn: null,
    talkBtn: null,
    noteEl: null,
    micScale: 1,
    outScale: 1,
    micLevel: 0,
    outLevel: 0,
    clientId: "",
    queue: [],
    lastUrl: "",
    running: false,
    opts: {}
  };

  function note(text) {
    if (state.noteEl) state.noteEl.textContent = text || "";
  }

  function ensureCtx() {
    if (!AudioCtx) return null;
    if (!state.ctx) {
      try { state.ctx = new AudioCtx(); } catch (err) { state.ctx = null; }
    }
    if (state.ctx && state.ctx.state === "suspended" && state.ctx.resume) {
      try { state.ctx.resume(); } catch (err2) {}
    }
    return state.ctx;
  }

  /* ---- microphone (blue) ------------------------------------------------ */

  function micSupported() {
    var md = root.navigator && root.navigator.mediaDevices;
    return !!(md && md.getUserMedia && AudioCtx);
  }

  function enableMic() {
    if (state.mic.active) return;
    if (!root.isSecureContext && root.location.hostname !== "localhost" && root.location.hostname !== "127.0.0.1") {
      note("Mic orb needs HTTPS or localhost. It still follows the hub microphone.");
      setMicButton(false, "Mic orb: needs HTTPS");
      return;
    }
    if (!micSupported()) {
      note("This browser has no microphone API. The blue orb follows the hub microphone instead.");
      setMicButton(false, "Mic orb: unavailable");
      return;
    }
    var ctx = ensureCtx();
    if (!ctx) { setMicButton(false, "Mic orb: unavailable"); return; }
    root.navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }).then(function (stream) {
      var src = ctx.createMediaStreamSource(stream);
      var an = ctx.createAnalyser();
      an.fftSize = 1024;
      an.smoothingTimeConstant = 0.5;
      src.connect(an); /* not connected to destination: no feedback */
      state.mic.analyser = an;
      state.mic.buf = new Uint8Array(an.fftSize);
      state.mic.stream = stream;
      state.mic.active = true;
      setMicButton(true, "Mic orb: on");
      note("");
    }, function (err) {
      var why = err && err.name ? err.name : "blocked";
      note("Microphone not available (" + why + "). The blue orb follows the hub microphone instead.");
      setMicButton(false, "Mic orb: " + (why === "NotAllowedError" ? "denied" : "no mic"));
    });
  }

  function disableMic() {
    var s = state.mic.stream;
    if (s && s.getTracks) {
      var tracks = s.getTracks();
      for (var i = 0; i < tracks.length; i++) tracks[i].stop();
    }
    state.mic.stream = null;
    state.mic.analyser = null;
    state.mic.active = false;
    setMicButton(false, "Mic orb: off");
  }

  function setMicButton(on, label) {
    if (!state.micBtn) return;
    state.micBtn.textContent = label;
    state.micBtn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  /* ---- talkback audio (orange) ------------------------------------------- */

  function hookAudio() {
    var el = state.audioEl;
    if (!el || state.out.hooked) return;
    var ctx = ensureCtx();
    if (!ctx || !ctx.createMediaElementSource) return;
    try {
      var src = ctx.createMediaElementSource(el);
      var an = ctx.createAnalyser();
      an.fftSize = 1024;
      an.smoothingTimeConstant = 0.5;
      src.connect(an);
      an.connect(ctx.destination); /* keep the audio audible */
      state.out.source = src;
      state.out.analyser = an;
      state.out.buf = new Uint8Array(an.fftSize);
      state.out.hooked = true;
    } catch (err) {
      /* Already hooked, or the browser refuses: fall back to the synthetic pulse. */
      state.out.analyser = null;
    }
  }

  function enableTalkback() {
    state.talkbackOn = true;
    try { root.localStorage.setItem("jarvis_talkback", "1"); } catch (e) {}
    hookAudio(); /* must happen inside the click so the AudioContext may start */
    if (state.talkBtn) {
      state.talkBtn.textContent = "Talkback: on";
      state.talkBtn.setAttribute("aria-pressed", "true");
    }
    say("Talkback is on.");
  }

  function disableTalkback() {
    state.talkbackOn = false;
    state.queue = [];
    try { root.localStorage.setItem("jarvis_talkback", "0"); } catch (e) {}
    if (state.audioEl) { try { state.audioEl.pause(); } catch (e2) {} }
    if (state.talkBtn) {
      state.talkBtn.textContent = "Talkback: off";
      state.talkBtn.setAttribute("aria-pressed", "false");
    }
  }

  function fetchWav(text, cb) {
    var J = root.Jarvis;
    var xhr = new XMLHttpRequest();
    xhr.open("POST", J ? J.apiUrl("/api/tts") : "/api/tts", true);
    xhr.responseType = "blob";
    var tok = "";
    try { tok = root.localStorage.getItem("jarvis_token") || ""; } catch (e) {}
    if (tok) xhr.setRequestHeader("X-Hub-Token", tok);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onload = function () {
      if (xhr.status === 200) { cb(null, xhr.response); return; }
      var msg = "Talkback unavailable (" + xhr.status + ")";
      var r = new FileReader();
      r.onload = function () {
        try { msg = JSON.parse(r.result).error || msg; } catch (e2) {}
        cb(msg);
      };
      r.onerror = function () { cb(msg); };
      try { r.readAsText(xhr.response); } catch (e3) { cb(msg); }
    };
    xhr.onerror = function () { cb("Could not reach the hub for talkback."); };
    xhr.send(JSON.stringify({ text: text }));
  }

  function playNext() {
    var el = state.audioEl;
    if (!el || !state.queue.length) return;
    if (!el.paused && !el.ended) return; /* ended handler will call us again */
    var text = state.queue.shift();
    fetchWav(text, function (err, blob) {
      if (err) { note(err); return playNext(); }
      note("");
      if (state.lastUrl && root.URL && root.URL.revokeObjectURL) root.URL.revokeObjectURL(state.lastUrl);
      state.lastUrl = root.URL.createObjectURL(blob);
      el.src = state.lastUrl;
      var p = el.play();
      if (p && p.catch) p.catch(function () { note("Tap Talkback to allow sound."); });
    });
  }

  function say(text) {
    if (!state.talkbackOn || !text) return;
    ensureCtx();
    state.queue.push(String(text));
    while (state.queue.length > 2) state.queue.shift(); /* never fall far behind */
    playNext();
  }

  /* ---- animation loop ---------------------------------------------------- */

  function targetFor(kind, nowMs) {
    var level = 0;
    var an = kind === "mic" ? state.mic : state.out;
    if (an.analyser && (kind === "mic" || !state.audioEl || !state.audioEl.paused)) {
      an.analyser.getByteTimeDomainData(an.buf);
      level = rmsOfBytes(an.buf);
      if (kind === "mic" && level >= SPEECH_THRESHOLD) return { scale: scaleForLevel(level), active: true, level: level };
      if (kind === "out" && level >= 0.004) return { scale: scaleForLevel(level, 0.004), active: true, level: level };
    }
    var hs = state.hubState;
    if (kind === "mic" && hs === "capturing") {
      return { scale: 1 + (MAX_SCALE - 1) * 0.8 * easeInOutSine(sinePulse(nowMs, 1.8)), active: true, level: 0.1 };
    }
    if (kind === "mic" && hs === "processing") {
      return { scale: 1 + 0.12 * easeInOutSine(sinePulse(nowMs, 0.9)), active: true, level: 0.03 };
    }
    var playing = state.audioEl && !state.audioEl.paused && !state.audioEl.ended;
    if (kind === "out" && (hs === "speaking" || (playing && !state.out.analyser))) {
      return { scale: 1 + (MAX_SCALE - 1) * 0.75 * easeInOutSine(sinePulse(nowMs, 2.4)), active: true, level: 0.1 };
    }
    return { scale: 1 + IDLE_BREATH * Math.sin(2 * Math.PI * nowMs / 4200 + (kind === "mic" ? 0 : 1.7)), active: false, level: 0 };
  }

  function paint(el, label, kind, nowMs) {
    var t = targetFor(kind, nowMs);
    var key = kind === "mic" ? "micScale" : "outScale";
    state[key] += (t.scale - state[key]) * SMOOTH;
    var s = state[key];
    el.style.transform = "scale(" + s.toFixed(3) + ")";
    el.style.opacity = t.active ? "1" : "0.6";
    var cls = "orb orb-" + (kind === "mic" ? "blue" : "orange") + (t.active ? " is-active" : "");
    if (el.className !== cls) el.className = cls;
    if (label) {
      var text = kind === "mic"
        ? (t.active ? (state.hubState === "processing" ? "thinking" : "listening") : "mic")
        : (t.active ? "speaking" : "talkback");
      if (label.textContent !== text) label.textContent = text;
    }
  }

  function frame(nowMs) {
    if (state.micEl) paint(state.micEl, state.micLabel, "mic", nowMs);
    if (state.outEl) paint(state.outEl, state.outLabel, "out", nowMs);
    root.requestAnimationFrame(frame);
  }

  /* ---- DOM + public API --------------------------------------------------- */

  function el(tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  function build(dock, tv) {
    dock.className = "orb-dock" + (tv ? " orb-dock-tv" : "");
    dock.innerHTML = "";
    var micWrap = el("div", "orb-slot");
    state.micEl = el("div", "orb orb-blue");
    state.micEl.setAttribute("aria-hidden", "true");
    state.micLabel = el("span", "orb-label", "mic");
    micWrap.appendChild(state.micEl);
    micWrap.appendChild(state.micLabel);

    var outWrap = el("div", "orb-slot");
    state.outEl = el("div", "orb orb-orange");
    state.outEl.setAttribute("aria-hidden", "true");
    state.outLabel = el("span", "orb-label", "talkback");
    outWrap.appendChild(state.outEl);
    outWrap.appendChild(state.outLabel);

    var controls = el("div", "orb-controls");
    var btnClass = tv ? "tile focusable orb-btn" : "orb-btn";
    state.micBtn = el("button", btnClass, "Mic orb: off");
    state.micBtn.type = "button";
    state.micBtn.setAttribute("aria-pressed", "false");
    state.micBtn.addEventListener("click", function () {
      if (state.mic.active) disableMic(); else enableMic();
    });
    state.talkBtn = el("button", btnClass, "Talkback: off");
    state.talkBtn.type = "button";
    state.talkBtn.setAttribute("aria-pressed", "false");
    state.talkBtn.addEventListener("click", function () {
      if (state.talkbackOn) disableTalkback(); else enableTalkback();
    });
    controls.appendChild(state.micBtn);
    controls.appendChild(state.talkBtn);
    state.noteEl = el("div", "orb-note");
    controls.appendChild(state.noteEl);

    dock.appendChild(micWrap);
    dock.appendChild(outWrap);
    dock.appendChild(controls);

    if (!state.audioEl) {
      state.audioEl = doc.getElementById("talkbackAudio");
      if (!state.audioEl) {
        state.audioEl = doc.createElement("audio");
        state.audioEl.id = "talkbackAudio";
        doc.body.appendChild(state.audioEl);
      }
    }
    state.audioEl.addEventListener("ended", playNext);
    state.audioEl.addEventListener("error", function () { playNext(); });
  }

  function init(opts) {
    opts = opts || {};
    state.opts = opts;
    state.clientId = opts.clientId || "";
    var dock = doc.getElementById(opts.dockId || "orbDock");
    if (!dock) return;
    build(dock, !!opts.tv);
    if (!root.requestAnimationFrame) {
      root.requestAnimationFrame = function (cb) { return root.setTimeout(function () { cb(Date.now()); }, 33); };
    }
    if (!state.running) {
      state.running = true;
      root.requestAnimationFrame(frame);
    }
    if (!root.isSecureContext && root.location.hostname !== "localhost" && root.location.hostname !== "127.0.0.1") {
      note("Mic orb needs HTTPS here; the blue orb will follow the hub microphone.");
    }
  }

  /* Hub state from the SSE "voice" event. */
  function onVoice(data) {
    if (!data) return;
    state.hubState = data.muted ? "muted" : (data.state || "idle");
  }

  /* Reply event: speak it on this device if talkback is on and the command came from here. */
  function onReply(data) {
    if (!data || !data.reply || !state.talkbackOn) return;
    if (state.clientId && data.client_id && data.client_id !== state.clientId) return;
    say(data.reply);
  }

  root.JarvisOrbs = {
    init: init,
    onVoice: onVoice,
    onReply: onReply,
    say: say,
    enableMic: enableMic,
    disableMic: disableMic,
    enableTalkback: enableTalkback,
    disableTalkback: disableTalkback,
    helpers: helpers,
    _state: state
  };
})(typeof window !== "undefined" ? window : this);
