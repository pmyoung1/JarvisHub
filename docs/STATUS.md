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

## Phase 2 should cover

- **Grok API** — enable `GrokSpecialist` as a low-priority catch-all
  for commands the rule layer cannot handle. Keep core function working
  if the network or the API is down.
- **TV root controller** — hook the resolved file path in
  `MediaSpecialist._play()` to the `samsung-tv-root` project so "play X
  on the TV" actually starts playback on the QN75QN800D.
- **Echo Show 15 and Fire TV voice remotes** — thin clients that submit
  the same `/api/command` contract (and optionally subscribe to SSE).
- **Laptop client** — a fuller desktop shell or a packaged view of the
  existing dashboard.
- Optional: HTTPS-by-default on the LAN, a tighter auth story, and
  richer media metadata (runtime from the container, better artwork
  fallbacks) if still missing.

## Explicitly out of scope (still)

- Smart-home device control, Home Assistant, MQTT, lighting, locks.
- Streaming the movie file through the hub.
- Cloud dependency for core function.

## Known limitations

- The Web Speech mic on a phone almost always needs HTTPS or localhost.
  Over `http://<lan-ip>` the page falls back to typing or the phone
  keyboard's dictation key. A self-signed cert script is provided but
  must be trusted manually.
- The Tizen Internet app is not a complete modern desktop browser.
  The TV page avoids bleeding-edge JavaScript and hover, but unusual
  Tizen quirks may still appear; bookmarking `/tv` is recommended.
- "Play on the TV" is a stub. It will not start a file on the Samsung.
- Filename parsing covers common scene / Kodi patterns; odd names or
  TV-episode schemes may need a later pass. `.nfo` wins when present.
- Incremental scans key off file and sidecar mtimes. If you only rename
  a folder, force a full rescan (`POST /api/media/rescan?full=1&wait=1`).
- Running the scheduled task as SYSTEM may be unable to read a movie
  library that lives in a user profile or on a drive that is not mounted
  until logon. Prefer the library owner's account in that case.
- Optional `auth_token` is a single shared household secret, not
  per-user authentication. SSE and the dashboard still assume a trusted
  LAN.
- `ThreadingHTTPServer` holds one thread per SSE client. Fine for a
  handful of household devices, not for a public deployment.
- No transcoding, no subtitle handling, no TV-show season/episode model.
