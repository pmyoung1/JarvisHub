/* Shared helpers for phone + TV dashboards. Avoid bleeding-edge JS. */
(function (root) {
  "use strict";

  function qs(sel, el) {
    return (el || document).querySelector(sel);
  }

  function token() {
    try {
      return window.localStorage.getItem("jarvis_token") || "";
    } catch (err) {
      return "";
    }
  }

  function apiUrl(path) {
    var t = token();
    if (!t) return path;
    return path + (path.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(t);
  }

  function request(method, path, body, cb) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, apiUrl(path), true);
    xhr.setRequestHeader("Accept", "application/json");
    var t = token();
    if (t) xhr.setRequestHeader("X-Hub-Token", t);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      var parsed = null;
      try {
        parsed = xhr.responseText ? JSON.parse(xhr.responseText) : {};
      } catch (err) {
        parsed = { ok: false, error: "bad json", raw: xhr.responseText };
      }
      if (typeof cb === "function") cb(xhr.status, parsed);
    };
    xhr.onerror = function () {
      if (typeof cb === "function") cb(0, { ok: false, error: "network error" });
    };
    if (body) {
      xhr.setRequestHeader("Content-Type", "application/json");
      xhr.send(JSON.stringify(body));
    } else {
      xhr.send();
    }
  }

  function sendCommand(text, source, clientId, cb) {
    request("POST", "/api/command", {
      text: text,
      source: source || "web",
      client_id: clientId || ""
    }, cb);
  }

  function connectEvents(onEvent, onState) {
    var url = apiUrl("/api/events");
    var es = null;
    var closed = false;

    function setState(ok, label) {
      if (typeof onState === "function") onState(ok, label);
    }

    function start() {
      if (closed) return;
      if (typeof EventSource === "undefined") {
        setState(false, "No live stream");
        return;
      }
      es = new EventSource(url);
      es.onopen = function () { setState(true, "Live"); };
      es.onerror = function () { setState(false, "Reconnecting"); };
      es.addEventListener("reply", function (ev) {
        var data;
        try { data = JSON.parse(ev.data); } catch (err) { return; }
        onEvent("reply", data);
      });
      es.addEventListener("media", function (ev) {
        var data;
        try { data = JSON.parse(ev.data); } catch (err) { return; }
        onEvent("media", data);
      });
      ["voice", "transfer", "mic-probe"].forEach(function (name) {
        es.addEventListener(name, function (ev) {
          var data;
          try { data = JSON.parse(ev.data); } catch (err) { return; }
          onEvent(name, data);
        });
      });
    }

    start();
    return {
      close: function () {
        closed = true;
        if (es) es.close();
      }
    };
  }

  function clientId(prefix) {
    var key = "jarvis_client_id";
    try {
      var existing = window.localStorage.getItem(key);
      if (existing) return existing;
      var made = (prefix || "web") + "-" + String(Date.now()) + "-" + Math.floor(Math.random() * 1e6);
      window.localStorage.setItem(key, made);
      return made;
    } catch (err) {
      return (prefix || "web") + "-temp";
    }
  }

  function posterUrl(id) {
    return apiUrl("/api/media/" + id + "/poster");
  }

  function labelOf(item) {
    if (!item) return "";
    return item.year ? (item.title + " (" + item.year + ")") : String(item.title || "");
  }

  root.Jarvis = {
    qs: qs,
    request: request,
    sendCommand: sendCommand,
    connectEvents: connectEvents,
    clientId: clientId,
    posterUrl: posterUrl,
    labelOf: labelOf,
    apiUrl: apiUrl
  };
})(window);
