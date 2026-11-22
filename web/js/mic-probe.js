/* ES5-friendly diagnostic for the Samsung TV Internet app.
   Tries the Tizen Product Microphone API, then getUserMedia. */
(function () {
  "use strict";

  var logEl = document.getElementById("log");
  var runBtn = document.getElementById("runBtn");
  var verdictEl = document.getElementById("verdict");
  var secureLine = document.getElementById("secureLine");
  var meterWrap = document.getElementById("meterWrap");
  var meterBar = document.getElementById("meterBar");
  var running = false;
  var report;

  function nowIso() {
    try { return new Date().toISOString(); } catch (e) { return String(new Date()); }
  }

  function errInfo(err) {
    if (!err) return { name: "Error", message: "unknown" };
    var out = {
      name: err.name || err.constructor && err.constructor.name || "Error",
      message: err.message || String(err),
      code: typeof err.code !== "undefined" ? err.code : ""
    };
    if (err.type) out.type = err.type;
    return out;
  }

  function addStep(kind, title, detail, extra) {
    var step = { kind: kind, title: title, detail: detail || "", ts: nowIso() };
    var key;
    if (extra) {
      for (key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) step[key] = extra[key];
      }
    }
    report.steps.push(step);
    var li = document.createElement("li");
    li.className = kind;
    li.appendChild(document.createTextNode(title + (detail ? " — " + detail : "")));
    logEl.appendChild(li);
    if (logEl.scrollTop !== undefined) logEl.scrollTop = logEl.scrollHeight;
    return step;
  }

  function setVerdict(ok, text) {
    verdictEl.className = "verdict " + (ok ? "ok" : "fail");
    verdictEl.firstChild
      ? (verdictEl.firstChild.nodeValue = text)
      : (verdictEl.textContent = text);
  }

  function loadWebapis(done) {
    var existing;
    try { existing = window.webapis; } catch (err) {
      done(err, "exception-reading-webapis");
      return;
    }
    if (existing && existing.microphone) {
      done(null, "already-present");
      return;
    }
    var script = document.createElement("script");
    script.type = "text/javascript";
    script.src = "$WEBAPIS/webapis/webapis.js";
    var settled = false;
    function finish(err, how) {
      if (settled) return;
      settled = true;
      done(err, how);
    }
    script.onload = function () { finish(null, "script-loaded"); };
    script.onerror = function () {
      finish(new Error("Failed to load $WEBAPIS/webapis/webapis.js"), "script-error");
    };
    try {
      document.getElementsByTagName("head")[0].appendChild(script);
    } catch (err) {
      finish(err, "script-append-failed");
      return;
    }
    setTimeout(function () {
      finish(new Error("Timed out loading webapis.js (expected in the TV Internet app)"), "timeout");
    }, 2500);
  }

  function constOr(name, fallback) {
    try {
      if (window.webapis && webapis.microphone && typeof webapis.microphone[name] !== "undefined") {
        return webapis.microphone[name];
      }
    } catch (err) {}
    return fallback;
  }

  function describeMic(mic) {
    var parts = [];
    try { if (mic.uid) parts.push("uid=" + mic.uid); } catch (e1) {}
    try { if (mic.name) parts.push("name=" + mic.name); } catch (e2) {}
    try { if (typeof mic.getName === "function") parts.push("getName=" + mic.getName()); } catch (e3) {}
    try { if (typeof mic.getUniqueId === "function") parts.push("getUniqueId=" + mic.getUniqueId()); } catch (e4) {}
    try { if (typeof mic.getDeviceId === "function") parts.push("deviceId=" + mic.getDeviceId()); } catch (e5) {}
    return parts.join(", ") || String(mic);
  }

  function probeTizen(done) {
    loadWebapis(function (loadErr, how) {
      var hasWebapis = false;
      var hasMic = false;
      try { hasWebapis = !!window.webapis; } catch (e0) {}
      try { hasMic = !!(window.webapis && webapis.microphone); } catch (e1) {}
      addStep(
        hasMic ? "ok" : "info",
        "Load $WEBAPIS/webapis/webapis.js",
        how + (loadErr ? "; " + loadErr.message : "") +
          "; webapis=" + hasWebapis + ", webapis.microphone=" + hasMic
      );
      if (!hasMic) {
        report.tizen_api = "absent";
        if (loadErr) report.errors.push(errInfo(loadErr));
        done();
        return;
      }
      try {
        if (typeof webapis.microphone.getVersion === "function") {
          addStep("ok", "webapis.microphone.getVersion()", String(webapis.microphone.getVersion()));
        }
      } catch (verErr) {
        addStep("fail", "webapis.microphone.getVersion()", verErr.message, errInfo(verErr));
        report.errors.push(errInfo(verErr));
      }
      if (typeof webapis.microphone.getMicrophones !== "function") {
        report.tizen_api = "fail";
        addStep("fail", "getMicrophones", "method is missing");
        done();
        return;
      }
      var finished = false;
      function finishTizen(status) {
        if (finished) return;
        finished = true;
        report.tizen_api = status;
        done();
      }
      try {
        webapis.microphone.getMicrophones(function (mics) {
          var list = mics || [];
          addStep("ok", "getMicrophones()", "count=" + list.length);
          if (!list.length) {
            addStep("info", "No Tizen microphones listed", "The Product API did not return a device.");
            finishTizen("fail");
            return;
          }
          var enabledAny = false;
          var i;
          for (i = 0; i < list.length; i++) {
            probeOneMic(list[i], i);
          }
          finishTizen(enabledAny ? "ok" : "fail");

          function probeOneMic(mic, index) {
            var uid = "";
            try { uid = mic.uid || (mic.getUniqueId && mic.getUniqueId()) || ""; } catch (eUid) {}
            addStep("info", "Microphone[" + index + "]", describeMic(mic));
            try {
              if (uid && typeof webapis.microphone.isConnected === "function") {
                var connected = webapis.microphone.isConnected(uid);
                addStep(connected ? "ok" : "fail", "isConnected(" + uid + ")", String(connected));
              }
            } catch (connErr) {
              addStep("fail", "isConnected()", connErr.message, errInfo(connErr));
              report.errors.push(errInfo(connErr));
            }
            var format = constOr("MICROPHONE_FORMAT_SIGNED_16BIT_LITTLE_ENDIAN", 0);
            var rate = constOr("MICROPHONE_SAMPLE_RATE_48000", constOr("MICROPHONE_FRAMERATE_48000", 48000));
            try {
              if (typeof mic.enableDevice === "function") {
                var ok = mic.enableDevice(format, rate);
                addStep(
                  ok ? "ok" : "fail",
                  "enableDevice(SIGNED_16BIT_LITTLE_ENDIAN, 48000)",
                  "returned " + ok + "; format=" + format + ", rate=" + rate
                );
                if (ok) {
                  enabledAny = true;
                  try { if (typeof mic.play === "function") mic.play(); } catch (playErr) {
                    addStep("fail", "play()", playErr.message, errInfo(playErr));
                  }
                  try { if (typeof mic.disableDevice === "function") mic.disableDevice(); } catch (disErr) {}
                }
              } else {
                addStep("fail", "enableDevice", "method is missing on the device object");
              }
            } catch (enErr) {
              addStep("fail", "enableDevice()", enErr.message, errInfo(enErr));
              report.errors.push(errInfo(enErr));
            }
          }
        }, function (apiErr) {
          addStep("fail", "getMicrophones onerror", apiErr && apiErr.message || String(apiErr), errInfo(apiErr));
          report.errors.push(errInfo(apiErr));
          finishTizen("fail");
        });
      } catch (getErr) {
        addStep("fail", "getMicrophones()", getErr.message, errInfo(getErr));
        report.errors.push(errInfo(getErr));
        finishTizen("fail");
      }
      setTimeout(function () {
        if (!finished) {
          addStep("fail", "getMicrophones()", "timed out waiting for the callback");
          finishTizen("fail");
        }
      }, 4000);
    });
  }

  function gumPrefix() {
    var n = navigator;
    if (n.mediaDevices && typeof n.mediaDevices.getUserMedia === "function") return "mediaDevices";
    if (typeof n.getUserMedia === "function") return "getUserMedia";
    if (typeof n.webkitGetUserMedia === "function") return "webkitGetUserMedia";
    if (typeof n.mozGetUserMedia === "function") return "mozGetUserMedia";
    return "";
  }

  function requestUserMedia(constraints, ok, fail) {
    var n = navigator;
    if (n.mediaDevices && typeof n.mediaDevices.getUserMedia === "function") {
      try {
        var p = n.mediaDevices.getUserMedia(constraints);
        if (p && typeof p.then === "function") {
          p.then(ok, fail);
          return;
        }
      } catch (e1) { fail(e1); return; }
    }
    var legacy = n.getUserMedia || n.webkitGetUserMedia || n.mozGetUserMedia;
    if (typeof legacy === "function") {
      try { legacy.call(n, constraints, ok, fail); } catch (e2) { fail(e2); }
      return;
    }
    fail(new Error("getUserMedia is not available"));
  }

  function analyseStream(stream, done) {
    meterWrap.hidden = false;
    var peak = 0;
    var ctx = null;
    var source = null;
    var analyser = null;
    var timer = null;
    var started = new Date().getTime();
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) {
        addStep("fail", "Web Audio API", "AudioContext is not available");
        stopTracks(stream);
        done(0, false);
        return;
      }
      ctx = new AC();
      source = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      var data = new Uint8Array(analyser.fftSize);
      function tick() {
        analyser.getByteTimeDomainData(data);
        var i, dev, maxDev = 0;
        for (i = 0; i < data.length; i++) {
          dev = data[i] > 128 ? data[i] - 128 : 128 - data[i];
          if (dev > maxDev) maxDev = dev;
        }
        if (maxDev > peak) peak = maxDev;
        meterBar.style.width = Math.min(100, Math.round((maxDev / 128) * 100)) + "%";
        if (new Date().getTime() - started >= 3000) {
          cleanup();
          var live = peak >= 3;
          addStep(live ? "ok" : "fail", "AnalyserNode (~3s)", "peak deviation=" + peak + " (128 is max)");
          done(peak, live);
          return;
        }
        timer = setTimeout(tick, 80);
      }
      function cleanup() {
        if (timer) clearTimeout(timer);
        try { source.disconnect(); } catch (e1) {}
        try { if (ctx.close) ctx.close(); } catch (e2) {}
        stopTracks(stream);
      }
      tick();
    } catch (err) {
      addStep("fail", "AnalyserNode", err.message, errInfo(err));
      report.errors.push(errInfo(err));
      stopTracks(stream);
      done(0, false);
    }
  }

  function stopTracks(stream) {
    try {
      var tracks = stream.getTracks ? stream.getTracks() : [];
      var i;
      for (i = 0; i < tracks.length; i++) tracks[i].stop();
    } catch (e) {}
  }

  function probeGum(done) {
    var secure = false;
    try { secure = !!window.isSecureContext; } catch (e0) {}
    report.secure_context = secure;
    addStep("info", "window.isSecureContext", String(secure) + " (getUserMedia usually needs HTTPS or localhost)");
    var which = gumPrefix();
    if (!which) {
      report.getusermedia = "absent";
      addStep("fail", "getUserMedia", "No MediaDevices or legacy getUserMedia / webkitGetUserMedia");
      done();
      return;
    }
    addStep("info", "getUserMedia entry", which);
    if (navigator.mediaDevices && typeof navigator.mediaDevices.enumerateDevices === "function") {
      var en = navigator.mediaDevices.enumerateDevices();
      if (en && typeof en.then === "function") {
        en.then(function (devs) {
          var labels = [];
          var i;
          for (i = 0; i < (devs || []).length; i++) {
            labels.push((devs[i].kind || "?") + ":" + (devs[i].label || devs[i].deviceId || "id hidden"));
          }
          addStep("info", "enumerateDevices()", labels.join("; ") || "none");
          afterEnum();
        }, function () { afterEnum(); });
        return;
      }
    }
    afterEnum();

    function afterEnum() {
      requestUserMedia({ audio: true }, function (stream) {
        report.getusermedia = "ok";
        addStep("ok", "getUserMedia({audio:true})", "stream received via " + which);
        analyseStream(stream, function (peak, live) {
          report.peak_amplitude = peak;
          if (live) report.live_audio = true;
          done();
        });
      }, function (err) {
        report.getusermedia = secure ? "fail" : "insecure";
        addStep("fail", "getUserMedia({audio:true})", (err && err.message) || String(err), errInfo(err));
        report.errors.push(errInfo(err));
        done();
      });
    }
  }

  function postReport() {
    var body = JSON.stringify(report);
    var token = "";
    try {
      var match = (location.search || "").match(/[?&]token=([^&]+)/);
      if (match) token = decodeURIComponent(match[1]);
      if (!token) token = window.localStorage.getItem("jarvis_token") || "";
    } catch (eTok) {}
    var url = "/api/mic-probe";
    if (token) url += "?token=" + encodeURIComponent(token);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    if (token) xhr.setRequestHeader("X-Hub-Token", token);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        addStep("ok", "Posted report to /api/mic-probe", "HTTP " + xhr.status);
      } else {
        addStep("fail", "Posted report to /api/mic-probe", "HTTP " + xhr.status);
      }
    };
    try { xhr.send(body); } catch (err) {
      addStep("fail", "Posted report to /api/mic-probe", err.message);
    }
  }

  function finish() {
    running = false;
    runBtn.disabled = false;
    if (report.live_audio) {
      setVerdict(true, "Live audio");
      report.notes = "A path returned live non-zero amplitude. Re-check on the real TV before treating this set as a listener.";
    } else {
      setVerdict(false, "No live audio");
      report.notes = "No path returned live audio. Treat the TV as display-only; phones and the Echo Show remain the primary ears.";
    }
    addStep("info", "Decision", report.notes);
    postReport();
  }

  function startProbe() {
    if (running) return;
    running = true;
    runBtn.disabled = true;
    logEl.innerHTML = "";
    report = {
      client_ts: nowIso(),
      user_agent: navigator.userAgent || "",
      secure_context: false,
      protocol: location.protocol,
      href: location.href,
      tizen_api: "absent",
      getusermedia: "absent",
      live_audio: false,
      peak_amplitude: 0,
      steps: [],
      errors: [],
      notes: ""
    };
    setVerdict(false, "Running");
    verdictEl.className = "verdict pending";
    verdictEl.textContent = "Running";
    probeTizen(function () {
      probeGum(finish);
    });
  }

  function describeSecure() {
    var secure = false;
    try { secure = !!window.isSecureContext; } catch (e) {}
    secureLine.textContent =
      (secure ? "Secure context" : "Not a secure context") +
      " · " + location.protocol + "//" + location.host +
      " · " + (navigator.userAgent || "").slice(0, 80);
  }

  runBtn.onclick = startProbe;
  document.onkeydown = function (ev) {
    var key = ev.keyCode || ev.which;
    if (key === 13) startProbe();
    if (key === 10009 || key === 8 || key === 27) {
      ev.preventDefault();
      location.href = "/tv";
    }
  };
  describeSecure();
  runBtn.focus();
})();
