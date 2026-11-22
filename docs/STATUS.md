# Status — phase 1 shipped, phase 2 implemented, later work

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
- Pluggable specialists: rule-based core, media, and a Grok placeholder
  that stays disabled without an API key.
- Media index: recursive video scan, filename / folder title+year
  parsing, Kodi `.nfo` and sidecar posters, SQLite, incremental mtime
  rescan, background scan, search and poster endpoints, fuzzy title
  match. No arbitrary file reads.
- README (Hub PC, phone, TV), architecture notes, this status file.
- Unit tests for filename parsing, routing, and indexing.

## Phase 2 implementation progress

Design: [PHASE2_DESIGN.md](PHASE2_DESIGN.md). Thin-client notes:
[CLIENTS.md](CLIENTS.md). TV mic diagnostic: [TV_MIC_PROBE.md](TV_MIC_PROBE.md).

Status values: **done** (automated tests on this box),
**implemented-untested-on-hardware** (code and tests with mocks / fakes;
needs a check on the Hub PC or the TV), **pending**.

| # | Milestone | Status | How it was verified here |
| - | --------- | ------ | ------------------------ |
| 1 | Voice agent process + TTS talkback (session split) | **implemented-untested-on-hardware** | `VoicePipeline` / `VoiceAgent` unit tests with fake TTS; localhost protocol test posts a command and “speaks” the reply. Piper, SAPI, sounddevice, and real speakers were **not** exercised. |
| 2 | Grok specialist | **done** (mocked HTTP only) | Unit tests with a fake `http_post`. Offline fallback, tool call for `play_media`, `/responses` and `/chat/completions` shapes. The real xAI API was **never** called (no key on this box). |
| 3 | Wake word + STT | **implemented-untested-on-hardware** | Fake wake engine + fake STT in unit and protocol tests, including a simulated wake frame then a transcript. openWakeWord, faster-whisper, and Vosk were not loaded (optional extras). |
| 4 | Speaker verification and mute / listening indicator | **implemented-untested-on-hardware** | Enrollment store + cosine tests with a hash encoder; reject path; skip-if-unenrolled; `POST /api/voice/mute` and dashboard mute button. SpeechBrain / Resemblyzer were not loaded. No real enrollment of Patrick's voice. |
| 5 | HTTP Range streaming + `TVController` interface | **implemented-untested-on-hardware** | Unit tests and curl: `206` / `Content-Range` / `Accept-Ranges`, 416, signed stream tokens, token-gated bare URLs. `NullController` keeps phase 1 `would_play` behavior. No real TV played a file. |
| 6 | `samsung-tv-root` adapter on the QN75QN800D | **implemented-untested-on-hardware** | Hook implemented (`command` or `endpoint` in `tv.controller_options`). Unconfigured hook honestly returns `would_play`. Endpoint hook tested against a local mock HTTP server. The external root project was not installed and the TV was not contacted. |
| 7 | Phone-to-TV drag-and-drop transfers | **implemented-untested-on-hardware** | Upload allowlist (extension + magic), size cap, list, delete, Range stream, play-through-controller, SSE events, phone drop zone + TV incoming row. No file was shown on the real TV. |
| 8 | Laptop shortcut / PWA, optional tray | **implemented-untested-on-hardware** | `/laptop` alias, `scripts/install_laptop_shortcut.ps1`, `python -m jarvis.tray` (tkinter). Not clicked on a Windows laptop here. |
| 9 | Echo Show 15 and Fire TV thin clients | **implemented-untested-on-hardware** | `/echo` and `/fire` aliases plus [CLIENTS.md](CLIENTS.md). No Alexa skill (would need public HTTPS :443). Not opened on those devices here. |
| 10 | Per-device tokens and optional LAN HTTPS | **done** (tokens); HTTPS script already existed | `auth_token` still works; `auth_tokens` map (plain or `sha256:…`); `X-Hub-Token` / Bearer / query. Stream URLs use short-lived HMAC query tokens. LAN HTTPS remains the existing cert script. |
| — | TV mic probe (diagnostic) | **implemented-untested-on-hardware** | `/tv/mic-probe` page, `POST`/`GET /api/mic-probe`, health summary. Endpoint tests below. **Not run on the QN75QN800D.** |

The hub process remains **stdlib-only**. Voice extras live in
`requirements-voice.txt` and are imported lazily.

### Hardware test checklist (Hub PC and TV)

Do these on the Windows Hub PC and the QN75QN800D after unzip / git
pull to `C:\JarvisHub`:

1. Hub still starts with no voice extras: `.\scripts\run.ps1`, then
   `http://127.0.0.1:8765/api/health`.
2. Install the voice agent in a **logged-on** session:
   `.\scripts\install_voice_agent.ps1`. Confirm the boot task
   `JarvisHub` is unchanged and the new task is `JarvisHubVoice` **At
   logon**, not Session 0.
3. `python -m jarvis.voice --list-devices` sees the mic and speakers.
   Set `voice.input_device` / `output_device` if the defaults are wrong.
4. Enroll Patrick's voice: `.\scripts\enroll_voice.ps1` (three clips).
   Confirm `data/voice/enrollments/profile.json` is written.
5. Say **“hey jarvis”** then a command. Confirm a reply is spoken on
   the PC speakers, the phone and `/tv` pages show the same reply, and
   a second speaker is rejected (or note the threshold).
6. Mute from the phone dashboard; confirm the agent stops listening.
   Unmute.
7. With `XAI_API_KEY` set, ask something the local specialists do not
   handle; confirm a Grok reply. Unset the key and confirm local
   fallback still works.
8. Play a library title. Confirm a stream URL is issued. Point
   `tv.controller` at samsung-tv-root when that project is installed
   and confirm the file actually starts on the TV. Until then,
   `would_play` is expected.
9. Upload a short video or photo from the phone Library tab; open it
   from the TV Incoming files row.
10. Open `/laptop`, the Echo Show Silk bookmark, and Fire TV `/fire`.
11. On the TV Internet app, open
    `http://<hub-ip>:8765/tv/mic-probe`, run the probe, then check
    `GET /api/health` → `mic_probe`. If `live_audio` is false, keep
    treating the TV as display-only.

## Phase 3 scope (design only — no phase 3 code in this tree)

Local, no-root Samsung TV control for the QN75QN800DFXZA (power on/off
and HDMI / named inputs) using the samsungtvws WebSocket remote, plus
the companions now noted in [PHASE3_DESIGN.md](PHASE3_DESIGN.md):

- Dual-orb TV overlay (blue listen / orange talkback), listener
  breadcrumb, and nearest-node mic handoff.
- Nighttime Reolink SD-card person detection (10 PM–6 AM, America/New_York).
- A later household security-scanning / hardening pass.
- Wake-on-LAN for power-on; `KEY_HDMI` / `KEY_SOURCE` input strategy.

Do **not** implement samsungtvws, power, or HDMI in phase 2.

### Phase 3 milestones

1. Optional package, config, pairing, token file, REST status.
2. Power off and WoL power on.
3. HDMI / named-input switching, calibrated on the QN75QN800D.
4. `TvSpecialist` (on/off, input, volume/mute) in the existing router.
5. Compose with the phase 2 playback hook (wake, then play).
6. Record what this firmware actually accepted for discrete HDMI keys.
7. Dual-orb UI + mic-node handoff.
8. Nighttime Reolink person detection.
9. Security scanning / hardening report.

## Explicitly out of scope (still)

- Smart-home device control, Home Assistant, MQTT, lighting, locks.
- Cloud dependency for **core** function (Grok and Alexa skills are
  optional and must fail open to local rules).
- Transcoding, a full TV-show season/episode model, and (unless later
  accepted) a public Alexa skill endpoint.
- Treating the TV as a listener until `/tv/mic-probe` proves live audio.

## Known limitations

- The Web Speech mic on a phone almost always needs HTTPS or localhost.
  Over `http://<lan-ip>` the page falls back to typing or the phone
  keyboard's dictation key. A self-signed cert script is provided but
  must be trusted manually.
- The Tizen Internet app is not a complete modern desktop browser.
  The TV page avoids bleeding-edge JavaScript and hover, but unusual
  Tizen quirks may still appear; bookmarking `/tv` is recommended.
- “Play on the TV” resolves a file and a Range stream URL. It will not
  start playback on the Samsung until `tv.controller_options` points at
  a working samsung-tv-root (or another) hook. DLNA advertisement is
  stubbed, not a full UPnP stack.
- Filename parsing covers common scene / Kodi patterns; odd names or
  TV-episode schemes may need a later pass. `.nfo` wins when present.
- Incremental scans key off file and sidecar mtimes. If you only rename
  a folder, force a full rescan (`POST /api/media/rescan?full=1&wait=1`).
- Running the scheduled task as SYSTEM may be unable to read a movie
  library that lives in a user profile or on a drive that is not mounted
  until logon. Prefer the library owner's account in that case.
- The voice agent **cannot** run inside the boot (Session 0) service.
  It needs an interactive logon (or autologon, which is a security
  tradeoff and is not the default).
- Wake-word engines are not speaker ID. Until Patrick enrolls, the
  agent skips verification and will accept any voice that says “hey
  jarvis”.
- `ThreadingHTTPServer` holds one thread per SSE client. Fine for a
  handful of household devices, not for a public deployment.
- No transcoding, no subtitle handling, no TV-show season/episode model.
