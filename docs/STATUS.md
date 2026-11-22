# Status — phase 1 vs. later work

## Done in phase 1

- Always-on Windows service scripts: Task Scheduler at boot, "run whether
  the user is logged on or not" (SYSTEM or a specified account), restart
  on failure, no execution time limit, plus uninstall and a Private-
  profile firewall rule. Startup-folder alternative is documented.
- Watchdog launcher around the HTTP server, rotating file logs under
  `data_dir`, `config.json` / `config.example.json`.
- Health (`GET /api/health`) and status endpoints.
- LAN web dashboard, no build step, no CDN:
  - Phone view: chat, Web Speech mic with graceful fallback, media grid
    and search, add-to-home-screen manifest.
  - `/tv` view for the Samsung QN75QN800D Tizen browser: large focusable
    tiles, spatial D-pad navigation, Enter, Back (keyCode 10009 /
    Backspace / Escape), rem/vw scaling, no hover-only UI.
- Command pipeline with explicit stages: wake → receive → route → reply
  → display. `POST /api/command` plus `GET /api/events` (SSE) so phone
  and TV stay in sync.
- Pluggable specialists: rule-based core, media (including a stub
  "would play on the TV" action and a marked `samsung-tv-root` hook),
  and a disabled `GrokSpecialist` placeholder.
- Media index: recursive video scan, filename / folder title+year
  parsing, Kodi `.nfo` and sidecar posters, SQLite, incremental mtime
  rescan, background scan, search and poster endpoints, fuzzy title
  match. No arbitrary file reads. No video streaming.
- README (Hub PC, phone, TV), architecture notes, this status file.
- Unit tests for filename parsing, routing, and indexing. The server
  was also exercised with curl against a fake library.

## Phase 2 scope

A full design, component choices, Windows setup, config keys, and
milestones live in [PHASE2_DESIGN.md](PHASE2_DESIGN.md). Summary:

- **Hub voice (interactive session only).** Always-on listening cannot
  run in the existing Session 0 scheduled task. A new voice agent starts
  **at logon** in the logged-on user session and talks to the hub over
  localhost HTTP / SSE.
- **Wake word:** openWakeWord (included “hey jarvis” model, fully
  local). Porcupine is not a fit for a personal household given current
  Picovoice plan terms and AccessKey validation.
- **Speaker verification:** a separate enrollment of Patrick’s voice
  (SpeechBrain ECAPA, Resemblyzer as fallback). A wake word alone is
  not voice ID.
- **STT:** faster-whisper on the 8 GB NVIDIA GPU, Vosk as a CPU
  fallback.
- **TTS:** Piper offline, Windows SAPI / pyttsx3 fallback. Not edge-tts
  (it needs the internet).
- **Grok API:** enable the existing disabled specialist as a catch-all,
  key from `XAI_API_KEY`, Responses API at `https://api.x.ai/v1`,
  timeouts, offline fallback, local function tools into current
  specialists.
- **TV playback:** a `TVController` adapter for `samsung-tv-root`, HTTP
  Range streaming at `/api/media/<id>/stream`, DLNA/UPnP as a non-root
  fallback.
- **Echo Show 15 / Fire TV:** Silk or a sideloaded WebView against the
  existing dashboard. An Alexa custom skill requires a public HTTPS
  endpoint and stays opt-in only, with that cloud tradeoff explicit.
- **Laptop client:** browser / PWA first, optional tray mute helper.
- **Phone-to-TV transfers:** upload to the hub with a size cap and TTL,
  then play or show on the TV through the same controller/stream path.

### Phase 2 milestones

1. Voice agent process + TTS talkback (proves the session split).
2. Grok specialist (can overlap with 1).
3. Wake word + STT.
4. Speaker verification and a mute / listening indicator.
5. HTTP Range streaming + `TVController` interface.
6. `samsung-tv-root` adapter on the QN75QN800D.
7. Phone-to-TV drag-and-drop transfers.
8. Laptop shortcut / PWA, then an optional tray app.
9. Echo Show 15 and Fire TV thin clients.
10. Per-device tokens and optional LAN HTTPS hardening.

## Phase 3 scope

Local, no-root Samsung TV control for the QN75QN800DFXZA (power on/off
and HDMI / named inputs) using the samsungtvws WebSocket remote. This
pairs with the phase 2 `samsung-tv-root` hook, which remains the path
for launching local movie files. The design, config keys, Windows and
TV setup, and the power-on Wake-on-LAN caveat are in
[PHASE3_DESIGN.md](PHASE3_DESIGN.md).

- **Power off** with `KEY_POWER` while the TV is on (check REST state
  first; the key is a toggle).
- **Power on** with Wake-on-LAN to the TV’s MAC (the WebSocket is down
  when the set is off). Enable Power On with Mobile / IP Remote.
- **Inputs** via `KEY_HDMI` / `KEY_SOURCE`, with `KEY_HDMI1`–`4` tested
  on this 2024 set before we rely on them. Named inputs (`Xbox`) live
  in config.
- Optional extra: `samsungtvws` is the first non-stdlib dependency for
  the hub process and stays optional.

### Phase 3 milestones

1. Optional package, config, pairing, token file, REST status.
2. Power off and WoL power on.
3. HDMI / named-input switching, calibrated on the QN75QN800D.
4. `TvSpecialist` (on/off, input, volume/mute) in the existing router.
5. Compose with the phase 2 playback hook (wake, then play).
6. Record what this firmware actually accepted for discrete HDMI keys.

## Explicitly out of scope (still)

- Smart-home device control, Home Assistant, MQTT, lighting, locks.
- Cloud dependency for **core** function (Grok and Alexa skills are
  optional and must fail open to local rules).
- Transcoding, a full TV-show season/episode model, and (unless later
  accepted) a public Alexa skill endpoint.

## Known limitations (phase 1, still true until the matching milestone)

- The Web Speech mic on a phone almost always needs HTTPS or localhost.
  Over `http://<lan-ip>` the page falls back to typing or the phone
  keyboard's dictation key. A self-signed cert script is provided but
  must be trusted manually.
- The Tizen Internet app is not a complete modern desktop browser.
  The TV page avoids bleeding-edge JavaScript and hover, but unusual
  Tizen quirks may still appear; bookmarking `/tv` is recommended.
- "Play on the TV" is a stub. It will not start a file on the Samsung
  until the phase 2 `TVController` work lands.
- Filename parsing covers common scene / Kodi patterns; odd names or
  TV-episode schemes may need a later pass. `.nfo` wins when present.
- Incremental scans key off file and sidecar mtimes. If you only rename
  a folder, force a full rescan (`POST /api/media/rescan?full=1&wait=1`).
- Running the scheduled task as SYSTEM may be unable to read a movie
  library that lives in a user profile or on a drive that is not mounted
  until logon. Prefer the library owner's account in that case.
- Optional `auth_token` is a single shared household secret, not
  per-user authentication. SSE and the dashboard still assume a trusted
  LAN. Phase 2 plans per-device tokens.
- `ThreadingHTTPServer` holds one thread per SSE client. Fine for a
  handful of household devices, not for a public deployment.
- No transcoding, no subtitle handling, no TV-show season/episode model.
