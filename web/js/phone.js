(function () {
  "use strict";

  var J = window.Jarvis;
  var clientId = J.clientId("phone");
  var logEl = J.qs("#log");
  var cmdEl = J.qs("#cmd");
  var micBtn = J.qs("#micBtn");
  var micNote = J.qs("#micNote");
  var conn = J.qs("#conn");
  var connLabel = J.qs("#connLabel");
  var mediaGrid = J.qs("#mediaGrid");
  var mediaQ = J.qs("#mediaQ");
  var mediaEmpty = J.qs("#mediaEmpty");
  var detail = J.qs("#detail");
  var selected = null;
  var rec = null;
  var listening = false;
  var searchTimer = null;

  function addBubble(role, text, meta) {
    var el = document.createElement("div");
    el.className = "bubble " + role;
    if (meta) {
      var m = document.createElement("span");
      m.className = "meta";
      m.textContent = meta;
      el.appendChild(m);
    }
    var body = document.createElement("div");
    body.textContent = text;
    el.appendChild(body);
    logEl.appendChild(el);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setConn(ok, label) {
    conn.className = "status-pill " + (ok ? "ok" : "bad");
    connLabel.textContent = label;
  }

  function applyReply(data) {
    if (!data || !data.reply) return;
    addBubble("hub", data.reply, data.intent || "reply");
    if (data.data && data.data.items && data.intent && data.intent.indexOf("media") === 0) {
      /* Keep the library tab in sync if a list/find came back. */
    }
  }

  J.qs("#composer").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = (cmdEl.value || "").trim();
    if (!text) return;
    cmdEl.value = "";
    addBubble("user", text, "you");
    J.sendCommand(text, "phone", clientId, function (status, body) {
      if (status === 0) addBubble("hub", "Could not reach the hub.", "error");
      else if (status >= 400) addBubble("hub", (body && body.error) || "Request failed.", "error");
      /* Successful replies also arrive over SSE; avoid double-append by
         only falling back if the event stream is down. */
      else if (conn.className.indexOf("ok") === -1 && body) applyReply(body);
    });
  });

  J.connectEvents(function (type, data) {
    if (type === "reply") applyReply(data);
    if (type === "media") loadMedia(mediaQ.value || "");
    if (type === "voice" && window._jarvisApplyVoice) window._jarvisApplyVoice(data);
    if (type === "transfer" && window._jarvisLoadTransfers) window._jarvisLoadTransfers();
  }, setConn);

  J.request("GET", "/api/status", null, function (status, body) {
    if (status === 200 && body && body.config && body.config.hub_name) {
      J.qs("#hubName").textContent = body.config.hub_name;
      document.title = body.config.hub_name + " Hub";
    }
    if (status === 200) {
      addBubble(
        "hub",
        "Online. " + (body.media_count || 0) + " titles indexed. Say help to see commands.",
        "status"
      );
    }
  });

  /* Tabs */
  function showTab(name) {
    var chat = name === "chat";
    J.qs("#panel-chat").className = "panel" + (chat ? " active" : "");
    J.qs("#panel-media").className = "panel" + (chat ? "" : " active");
    J.qs("#panel-chat").hidden = !chat;
    J.qs("#panel-media").hidden = chat;
    J.qs("#tab-chat").setAttribute("aria-selected", chat ? "true" : "false");
    J.qs("#tab-media").setAttribute("aria-selected", chat ? "false" : "true");
    if (!chat) loadMedia(mediaQ.value || "");
  }
  J.qs("#tab-chat").addEventListener("click", function () { showTab("chat"); });
  J.qs("#tab-media").addEventListener("click", function () { showTab("media"); });

  function loadMedia(q) {
    J.request("GET", "/api/media?q=" + encodeURIComponent(q || "") + "&limit=60", null, function (status, body) {
      mediaGrid.innerHTML = "";
      var items = (body && body.items) || [];
      mediaEmpty.hidden = items.length > 0;
      for (var i = 0; i < items.length; i++) {
        (function (item) {
          var card = document.createElement("button");
          card.type = "button";
          card.className = "card";
          var img = document.createElement("img");
          img.alt = "";
          img.src = J.posterUrl(item.id);
          var cap = document.createElement("div");
          cap.className = "cap";
          var strong = document.createElement("strong");
          strong.textContent = item.title || "Untitled";
          var span = document.createElement("span");
          span.textContent = item.year ? String(item.year) : "";
          cap.appendChild(strong);
          cap.appendChild(span);
          card.appendChild(img);
          card.appendChild(cap);
          card.addEventListener("click", function () { openDetail(item); });
          mediaGrid.appendChild(card);
        })(items[i]);
      }
    });
  }

  mediaQ.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { loadMedia(mediaQ.value); }, 180);
  });

  J.qs("#rescanBtn").addEventListener("click", function () {
    J.qs("#rescanBtn").disabled = true;
    J.request("POST", "/api/media/rescan?wait=1", null, function () {
      J.qs("#rescanBtn").disabled = false;
      loadMedia(mediaQ.value || "");
    });
  });

  function openDetail(item) {
    selected = item;
    J.request("GET", "/api/media/" + item.id, null, function (status, body) {
      var full = (body && body.item) || item;
      selected = full;
      J.qs("#detailTitle").textContent = full.title || "Untitled";
      J.qs("#detailYear").textContent = full.year ? String(full.year) : "";
      J.qs("#detailPlot").textContent = full.plot || "No plot on file.";
      detail.hidden = false;
      detail.className = "detail open";
    });
  }
  function closeDetail() {
    detail.hidden = true;
    detail.className = "detail";
  }
  J.qs("#detailClose").addEventListener("click", closeDetail);
  detail.addEventListener("click", function (ev) {
    if (ev.target === detail) closeDetail();
  });
  J.qs("#detailPlay").addEventListener("click", function () {
    if (!selected) return;
    showTab("chat");
    closeDetail();
    var text = "play " + selected.title + " on the tv";
    addBubble("user", text, "you");
    J.sendCommand(text, "phone", clientId, function (status, body) {
      if (conn.className.indexOf("ok") === -1 && body) applyReply(body);
    });
  });

  /* Web Speech API — present on some desktop browsers and on HTTPS.
     Most mobile browsers block it on plain HTTP; the on-screen keyboard
     dictation button still works without HTTPS. */
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var secure = window.isSecureContext;
  if (!SR) {
    micBtn.disabled = true;
    micBtn.title = "Voice input is not available in this browser";
    micNote.textContent = "Voice buttons are unavailable here. Type, or use your keyboard's microphone key.";
  } else if (!secure && window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1") {
    micBtn.disabled = true;
    micBtn.title = "Microphone requires HTTPS or localhost";
    micNote.textContent = "The mic button needs HTTPS (or localhost). Use the phone keyboard's dictation key, or see the README for a self-signed certificate.";
  } else {
    micNote.textContent = "Tap the mic to speak a command.";
    rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = function (ev) {
      var said = ev.results && ev.results[0] && ev.results[0][0] ? ev.results[0][0].transcript : "";
      if (said) {
        cmdEl.value = said;
        J.qs("#composer").dispatchEvent(new Event("submit", { cancelable: true }));
      }
    };
    rec.onend = function () {
      listening = false;
      micBtn.className = "icon-btn";
    };
    rec.onerror = function () {
      listening = false;
      micBtn.className = "icon-btn";
    };
    micBtn.addEventListener("click", function () {
      if (listening) {
        try { rec.stop(); } catch (err) {}
        return;
      }
      try {
        rec.start();
        listening = true;
        micBtn.className = "icon-btn listening";
      } catch (err) {
        micNote.textContent = "Could not start the microphone.";
      }
    });
  }

  /* Phase 2: hub-mic mute toggle and phone-to-TV transfers. */
  var muteBtn = J.qs("#muteBtn");
  function applyVoice(state) {
    if (!muteBtn || !state) return;
    var muted = !!state.muted;
    muteBtn.textContent = muted ? "Mic: muted" : "Mic: " + (state.state || "listening");
    muteBtn.className = "status-pill" + (muted ? " muted" : "");
  }
  if (muteBtn) {
    muteBtn.addEventListener("click", function () {
      var muted = muteBtn.className.indexOf("muted") === -1;
      J.request("POST", "/api/voice/mute", { muted: muted }, function (status, body) {
        if (status === 200 && body) applyVoice(body);
      });
    });
  }
  J.request("GET", "/api/voice/status", null, function (status, body) {
    if (status === 200) applyVoice(body);
  });

  function loadTransfers() {
    var list = J.qs("#transferList");
    if (!list) return;
    J.request("GET", "/api/transfers", null, function (status, body) {
      list.innerHTML = "";
      var items = (body && body.items) || [];
      for (var i = 0; i < items.length; i++) {
        (function (item) {
          var row = document.createElement("button");
          row.type = "button";
          row.textContent = "Send to TV: " + (item.filename || item.public_id) + " (" + item.size + " bytes)";
          row.addEventListener("click", function () {
            J.request("POST", "/api/transfers/" + item.public_id + "/play", {}, function (st, resp) {
              showTab("chat");
              if (resp && resp.reply) addBubble("hub", resp.reply, "transfer");
            });
          });
          list.appendChild(row);
        })(items[i]);
      }
    });
  }
  loadTransfers();

  var drop = J.qs("#dropZone");
  function uploadFile(file) {
    if (!file) return;
    var xhr = new XMLHttpRequest();
    var url = "/api/transfers";
    try {
      var tok = window.localStorage.getItem("jarvis_token") || "";
      if (tok) {
        url += "?token=" + encodeURIComponent(tok);
        xhr.open("POST", url, true);
        xhr.setRequestHeader("X-Hub-Token", tok);
      } else {
        xhr.open("POST", url, true);
      }
    } catch (err) {
      xhr.open("POST", url, true);
    }
    xhr.setRequestHeader("X-Filename", file.name);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    drop.textContent = "Uploading " + file.name + "…";
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        drop.textContent = "Uploaded " + file.name + ". Open it on the TV incoming-files row, or tap it below.";
        loadTransfers();
      } else {
        var msg = "Upload failed";
        try { msg = (JSON.parse(xhr.responseText) || {}).error || msg; } catch (e) {}
        drop.textContent = msg;
      }
    };
    xhr.send(file);
  }
  if (drop) {
    drop.addEventListener("dragover", function (ev) { ev.preventDefault(); drop.className = "drop-zone drag"; });
    drop.addEventListener("dragleave", function () { drop.className = "drop-zone"; });
    drop.addEventListener("drop", function (ev) {
      ev.preventDefault();
      drop.className = "drop-zone";
      var files = ev.dataTransfer && ev.dataTransfer.files;
      if (files && files[0]) uploadFile(files[0]);
    });
    drop.addEventListener("click", function () {
      var input = document.createElement("input");
      input.type = "file";
      input.accept = "video/*,image/*,audio/wav,audio/mpeg";
      input.onchange = function () { if (input.files && input.files[0]) uploadFile(input.files[0]); };
      input.click();
    });
  }


  window._jarvisApplyVoice = applyVoice;
  window._jarvisLoadTransfers = loadTransfers;
})();
