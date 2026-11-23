/* Samsung QN75QN800D / Tizen 8-9 TV remote page.
   Spatial D-pad navigation, Enter to select, Back (10009 / Backspace / Escape).
   No hover-only behavior. Avoid bleeding-edge JS. The Tizen browser
   typically has no Web Speech API — use tiles or the on-screen keyboard. */
(function () {
  "use strict";

  var J = window.Jarvis;
  var clientId = J.clientId("tv");
  var cmdEl = J.qs("#cmd");
  var replyText = J.qs("#replyText");
  var posterRow = J.qs("#posterRow");
  var overlay = J.qs("#overlay");
  var selected = null;
  var overlayOpen = false;

  function setLive(ok, label) {
    var el = J.qs("#live");
    el.className = "live" + (ok ? "" : " off");
    el.textContent = label;
  }

  function setReply(text) {
    replyText.textContent = text;
  }

  function tickClock() {
    var now = new Date();
    var h = now.getHours();
    var m = now.getMinutes();
    var ampm = h >= 12 ? "PM" : "AM";
    var hr = h % 12;
    if (hr === 0) hr = 12;
    var mm = m < 10 ? "0" + m : String(m);
    J.qs("#clock").textContent = hr + ":" + mm + " " + ampm;
  }
  tickClock();
  setInterval(tickClock, 15000);

  function send(text) {
    if (!text) return;
    setReply("…");
    J.sendCommand(text, "tv", clientId, function (status, body) {
      if (status === 0) setReply("Could not reach the hub.");
      else if (status >= 400) setReply((body && body.error) || "Request failed.");
      else if (J.qs("#live").className.indexOf("off") >= 0 && body && body.reply) {
        setReply(body.reply);
      }
    });
  }

  J.qs("#composer").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = (cmdEl.value || "").trim();
    if (!text) return;
    cmdEl.value = "";
    send(text);
  });

  var actions = J.qs("#quickActions");
  actions.addEventListener("click", function (ev) {
    var btn = ev.target;
    while (btn && btn !== actions && !btn.getAttribute("data-cmd")) btn = btn.parentNode;
    if (btn && btn.getAttribute("data-cmd")) send(btn.getAttribute("data-cmd"));
  });

  if (window.JarvisOrbs) window.JarvisOrbs.init({ dockId: "orbDock", clientId: clientId, tv: true });

  J.connectEvents(function (type, data) {
    if (window.JarvisOrbs) {
      if (type === "voice") window.JarvisOrbs.onVoice(data);
      if (type === "reply") window.JarvisOrbs.onReply(data);
    }
    if (type === "reply" && data && data.reply) setReply(data.reply);
    if (type === "media") loadPosters();
    if (type === "transfer") loadIncoming();
  }, setLive);

  J.request("GET", "/api/status", null, function (status, body) {
    if (status === 200 && body && body.config && body.config.hub_name) {
      J.qs("#hubName").textContent = body.config.hub_name;
      document.title = body.config.hub_name + " Hub — TV";
    }
  });

  function loadIncoming() {
    var row = J.qs("#incomingRow");
    var section = J.qs("#incomingSection");
    if (!row || !section) return;
    J.request("GET", "/api/transfers", null, function (status, body) {
      var items = (body && body.items) || [];
      row.innerHTML = "";
      section.hidden = items.length === 0;
      for (var i = 0; i < items.length; i++) {
        (function (item) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "tile focusable";
          btn.textContent = item.filename || "file";
          btn.addEventListener("click", function () {
            J.request("POST", "/api/transfers/" + item.public_id + "/play", {}, function (st, resp) {
              if (resp && resp.reply) setReply(resp.reply);
            });
          });
          row.appendChild(btn);
        })(items[i]);
      }
    });
  }
  loadIncoming();

  function loadPosters() {
    J.request("GET", "/api/media?limit=12", null, function (status, body) {
      posterRow.innerHTML = "";
      var items = (body && body.items) || [];
      J.qs("#libLabel").textContent = items.length ? "Library" : "Library (empty — add media_paths and rescan)";
      for (var i = 0; i < items.length; i++) {
        (function (item) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "poster-tile focusable";
          var img = document.createElement("img");
          img.alt = "";
          img.src = J.posterUrl(item.id);
          var cap = document.createElement("div");
          cap.className = "cap";
          cap.appendChild(document.createTextNode(item.title || "Untitled"));
          var span = document.createElement("span");
          span.textContent = item.year ? String(item.year) : "";
          cap.appendChild(span);
          btn.appendChild(img);
          btn.appendChild(cap);
          btn.addEventListener("click", function () { openOverlay(item); });
          posterRow.appendChild(btn);
        })(items[i]);
      }
    });
  }
  loadPosters();

  function openOverlay(item) {
    selected = item;
    J.request("GET", "/api/media/" + item.id, null, function (status, body) {
      var full = (body && body.item) || item;
      selected = full;
      J.qs("#ovTitle").textContent = full.title || "Untitled";
      J.qs("#ovMeta").textContent = full.year ? String(full.year) : "";
      J.qs("#ovPlot").textContent = full.plot || "No plot on file.";
      overlay.className = "overlay open";
      overlayOpen = true;
      J.qs("#ovPlay").focus();
    });
  }

  function closeOverlay() {
    overlay.className = "overlay";
    overlayOpen = false;
    var firstPoster = J.qs(".poster-tile");
    if (firstPoster) firstPoster.focus();
    else J.qs("#firstTile").focus();
  }

  J.qs("#ovClose").addEventListener("click", closeOverlay);
  J.qs("#ovPlay").addEventListener("click", function () {
    if (!selected) return;
    var title = selected.title || "";
    closeOverlay();
    send("play " + title + " on the tv");
  });

  /* ---- Spatial navigation --------------------------------------------- */

  function focusables() {
    var root = overlayOpen ? overlay : document;
    var list = root.querySelectorAll(".focusable");
    var out = [];
    for (var i = 0; i < list.length; i++) {
      if (!list[i].disabled && list[i].offsetParent !== null) out.push(list[i]);
    }
    return out;
  }

  function center(el) {
    var r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, r: r };
  }

  function inDirection(from, to, dir) {
    var a = from.r;
    var b = to.r;
    var slop = 8;
    if (dir === "left") return b.right <= a.left + slop;
    if (dir === "right") return b.left >= a.right - slop;
    if (dir === "up") return b.bottom <= a.top + slop;
    if (dir === "down") return b.top >= a.bottom - slop;
    return false;
  }

  function move(dir) {
    var items = focusables();
    if (!items.length) return;
    var current = document.activeElement;
    var idx = -1;
    for (var i = 0; i < items.length; i++) {
      if (items[i] === current) { idx = i; break; }
    }
    if (idx < 0) {
      items[0].focus();
      return;
    }
    var from = center(current);
    var best = null;
    var bestScore = Infinity;
    for (var j = 0; j < items.length; j++) {
      if (items[j] === current) continue;
      var to = center(items[j]);
      if (!inDirection(from, to, dir)) continue;
      var dx = to.x - from.x;
      var dy = to.y - from.y;
      var primary = (dir === "left" || dir === "right") ? Math.abs(dx) : Math.abs(dy);
      var cross = (dir === "left" || dir === "right") ? Math.abs(dy) : Math.abs(dx);
      var score = primary + cross * 3;
      if (score < bestScore) {
        bestScore = score;
        best = items[j];
      }
    }
    if (best) {
      best.focus();
      if (best.scrollIntoView) {
        try { best.scrollIntoView({ block: "nearest", inline: "nearest" }); }
        catch (err) { best.scrollIntoView(); }
      }
    }
  }

  function isTextField(el) {
    if (!el || !el.tagName) return false;
    var tag = el.tagName.toLowerCase();
    return tag === "input" || tag === "textarea";
  }

  /* Tizen Back is keyCode 10009. Also handle Backspace and Escape. */
  var BACK_CODES = { 10009: true, 8: true, 27: true };
  var ARROWS = { 37: "left", 38: "up", 39: "right", 40: "down" };

  document.addEventListener("keydown", function (ev) {
    var code = ev.keyCode || ev.which;
    var dir = ARROWS[code];
    if (dir) {
      /* Allow left/right caret movement when the input actually has text
         and is focused, but still move focus when the field is empty. */
      if (isTextField(document.activeElement) && (dir === "left" || dir === "right")) {
        var el = document.activeElement;
        var hasText = (el.value || "").length > 0;
        if (hasText) return;
      }
      ev.preventDefault();
      ev.stopPropagation();
      move(dir);
      return;
    }
    if (BACK_CODES[code]) {
      if (overlayOpen) {
        ev.preventDefault();
        ev.stopPropagation();
        closeOverlay();
        return;
      }
      if (isTextField(document.activeElement) && (document.activeElement.value || "")) {
        /* Let the input consume Backspace when it has text. Escape blurs. */
        if (code === 27) {
          ev.preventDefault();
          document.activeElement.blur();
          J.qs("#firstTile").focus();
        }
        return;
      }
      /* Stay on the page instead of backing out of the Internet app if we can. */
      ev.preventDefault();
      ev.stopPropagation();
    }
  }, true);

  /* Initial focus so a D-pad press has a starting point. */
  var first = J.qs("#firstTile");
  if (first) first.focus();
})();
