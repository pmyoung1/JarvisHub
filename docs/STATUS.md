# Status - phase 1 shipped, phase 2 in this tree, later work

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

## Phase 2: what is in this tree

Design: [PHASE2_DESIGN.md](PHASE2_DESIGN.md). Install:
[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md). Thin clients: [CLIENTS.md](CLIENTS.md).
TV mic diagnostic: [TV_MIC_PROBE.md](TV_MIC_PROBE.md). TV power/input:
[TV_CONTROL.md](TV_CONTROL.md).

Status values: **verified here** (run on the build box with the real library
where noted), **needs hardware** (code is tested, but only the Hub PC, its
microphone and speakers, or the TV can confirm it), **pending**.

| # | Feature | Files | Status | What was actually run |
| - | ------- | ----- | ------ | --------------------- |
| 1 | Wake word listener, offline "hey jarvis" -> STT -> `POST /api/command` | `scripts/wakeword.py`, `jarvis/voice/listener.py`, `capture.py`, `wake.py`, `stt.py`, `client.py`, `lock.py` | **verified here** except the live microphone | Real openWakeWord scored a Piper-spoken "Hey Jarvis" 0.94-0.996 and an unrelated sentence 0.0. Real faster-whisper (`tiny.en`, CPU) transcribed it. `wakeword.py --wav` then POSTed the text to a live hub and got the reply back. Unit tests cover the POST payload (path, JSON, `X-Hub-Token`), dry-run, mute, below-threshold, hub-down, lock. **Needs hardware:** `sounddevice` capture from the real mic, Windows audio device selection, GPU STT. |
| 2 | Listener launch: run, add to startup, remove | `scripts/run_wakeword.ps1`, `install_wakeword_startup.ps1`, `uninstall_wakeword_startup.ps1` | **needs hardware** (Windows) | All `.ps1` files parse under the PowerShell 7.7 parser and are ASCII-only (Windows PowerShell 5.1 safe). `run_wakeword.ps1` argument passing, log files and restart loop ran under PowerShell 7 on Linux against a stub Python. **Not run on Windows:** the Startup-folder shortcut (`WScript.Shell`), `Disable-ScheduledTask`, `Start-Process` redirection. |
| 3 | Talkback: Piper first, Windows SAPI fallback, no edge-tts | `jarvis/tts_service.py`, `jarvis/voice/tts.py`, `GET`/`POST /api/tts` | **verified here** for Piper; SAPI **needs hardware** | Real Piper (`en_US-lessac-medium`) produced valid WAVs through the HTTP endpoint (22.05 kHz). Fallback order, caching, 400/401/503 and the install hint are unit tested with fake engines. Found and fixed a bug: `PiperSpeaker` handed Piper a raw file instead of a WAV writer. **Needs hardware:** `System.Speech` and pyttsx3 on Windows, audible playback. |
| 4 | Orbs: blue mic, orange talkback, sine-eased radial gradients | `web/js/orbs.js`, `web/css/orbs.css`, `web/index.html`, `web/tv.html`, `phone.js`, `tv.js` | **verified here** in desktop Chrome; **needs hardware** on a phone and the TV | Headless Chrome with a fake microphone: the blue orb scale moved 0.97-1.32 with the beeps; after tapping Talkback the orange orb moved 0.97-1.44 with real Piper audio from `/api/tts`; no console errors. `/tv` renders the orbs. JS helpers (easing, RMS, scale) are tested under node. **Needs hardware:** phone Safari/Chrome over HTTPS, and the Tizen browser (which has no mic over HTTP, so the blue orb only follows hub state there). |
| 5 | TV mic probe | `web/mic-probe.html`, `web/js/mic-probe.js`, `jarvis/mic_probe.py`, `/api/mic-probe`, `/api/health` | **verified here** in desktop Chrome; **needs the TV** | Page loads at `/tv/mic-probe`, runs, posts a report (HTTP 201), and `/api/health` shows `mic_probe`. Doc now explains browser-served page vs packaged Tizen app and the privileges. **Not run on the QN75QN800DFXZA.** |
| 6 | Samsung TV control stubs: power off, Wake-on-LAN power on, input | `jarvis/tv/samsung.py`, `jarvis/tv/wol.py`, `POST /api/tv/power`, `/api/tv/input`, `python -m jarvis.tv.samsung` | **needs hardware** | Magic packet layout and UDP send are unit tested. The controller is tested against a mocked `samsungtvws` whose constructor and `send_key` signature were checked against the real installed package (3.0.7). `KEY_POWER`, `KEY_HDMI`, `KEY_SOURCE`, `wss:8002`, `token_file`, KEY_HDMI1-4 refusal, 501 when disabled, and a missing library are covered. **The TV was never contacted.** |
| 7 | Requirements | `requirements.txt` (core, no packages), `requirements-wakeword.txt`, `requirements-voice.txt`, `requirements-tv.txt` | **verified here** | `pip --platform win_amd64` resolves every package on Python 3.10-3.13 (openWakeWord itself was installed and run on Linux). A test fails if the core file gains packages. |
| 8 | Install guide and zip | `docs/INSTALL_WINDOWS.md`, `JarvisHub-phase2.zip` | **needs hardware** (Windows) | Script names, task names and requirement files in the guide are checked by a test. The commands were not executed on Windows. |
| 9 | Full voice agent (speaker verification, mute sync, spoken replies on the PC) | `jarvis/voice/agent.py`, `speaker.py`, `enroll.py`, `scripts/install_voice_agent.ps1`, `enroll_voice.ps1` | **needs hardware** | Fake-engine unit tests and a localhost protocol test, as before. Now shares the single-listener lock and publishes a `speaking` state for the orbs. SpeechBrain/Resemblyzer never loaded; nobody has enrolled. |
| 10a | samsung-tv-root archive-root play / tv on / tv off | `jarvis/tv/root.py`, `jarvis/specialists/tv.py`, [TV_ROOT.md](TV_ROOT.md) | **needs hardware** | Subprocess argv, timeout, missing-exe, push+launch sequence, and specialist replies are unit tested with mocks. **The live TV at 10.0.0.7 was not contacted from this box.** |
| 10 | Grok, Range streaming, transfers, tokens, thin clients | unchanged | as in the earlier phase 2 notes | Mocked HTTP only for Grok; no real TV or laptop/Echo/Fire client was used. |

Also fixed while running the real engines: faster-whisper on a CPU got the GPU
compute type `int8_float16` (so every CPU config looked "unavailable"), and its
temp-WAV path broke on a newer PyAV. STT now sends a float32 array and drops to
CPU if the CUDA runtime DLLs are missing. The hub-side `/api/tts` and
`install_service.ps1` prefer the project `.venv` Python so Piper can load.

The hub process remains **stdlib-only**. A test imports the hub, talkback, TV
and listener modules with every optional package blocked.

### Hardware test checklist (Hub PC and TV)

Do these on the Windows Hub PC and the QN75QN800DFXZA after following
[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md):

1. `Invoke-RestMethod http://localhost:8765/api/health` works with the
   `JarvisHub` task running, and `(Invoke-RestMethod http://localhost:8765/api/status).tts`
   shows `piper_package` and `piper_voice_present` as `True`.
2. `Invoke-WebRequest ".../api/tts?text=hello" -OutFile x.wav`, play it. Confirm
   a natural Piper voice, then temporarily rename `data\voices` and confirm the
   SAPI voice answers instead (`X-Jarvis-TTS-Engine` header).
3. `.\.venv\Scripts\python.exe scripts\wakeword.py --list-devices` shows the
   microphone. `run_wakeword.ps1 -Foreground --dry-run`: say "hey jarvis, what
   time is it" and see the transcript. Tune `--threshold` (0.5 default) and
   `voice.input_device` if it misses or false-triggers. Confirm the GPU is used
   or the log says it fell back to CPU.
4. Without `--dry-run`, the same phrase produces a reply on the phone and `/tv`.
5. `install_wakeword_startup.ps1`, log off and on, confirm `Jarvis Wakeword.lnk`
   starts it (`logs\wakeword.log`) and the listener hears you with no console
   open. Confirm running the voice agent too is refused (exit message about the
   other listener).
6. Mute from the phone dashboard; the listener stops reacting. Unmute.
7. Phone: tap **Talkback**, send a command, hear the reply; the orange orb
   moves. Over HTTPS (or `localhost`) tap **Mic orb**; the blue orb moves when
   you talk. On `/tv` check the focus ring reaches the orb buttons and the
   orange orb moves with talkback.
8. `http://<hub-ip>:8765/tv/mic-probe` on the TV, run it, then read `mic_probe`
   from `/api/health`. If `live_audio` is false keep treating the TV as
   display-only, and decide whether a packaged-app test is worth doing.
9. TV: enable **Power On with Mobile** and **IP Remote**, set `tv.ip`, `tv.mac`,
   `tv.controller`, run `python -m jarvis.tv.samsung --pair`, then `--off`,
   `--on` (from standby, on Ethernet and, if you care, Wi-Fi), `--input next`,
   `--input source`. Record what `KEY_HDMI` and `KEY_SOURCE` do on this firmware
   and whether `hdmi1`..`hdmi4` do anything.
10. Optional full voice agent: `enroll_voice.ps1`, `install_voice_agent.ps1`,
    confirm a second speaker is rejected.
11. With `XAI_API_KEY` set, ask something the local specialists do not handle.
12. Upload a short video or photo from the phone; open it from the TV Incoming
    row; open `/laptop`, the Echo Show bookmark, and Fire TV `/fire`.

## Phase 2↔3 bridge: samsung-tv-root

See [TV_ROOT.md](TV_ROOT.md). `jarvis/tv/root.py` shells out to the 0.0.13
release exe (`archive-root root <ip> --command …`) for real playback on the
already-rooted QN75QN800DFXZA. Power-on is Wake-on-LAN; power-off / input reuse
samsungtvws keys when installed. **Not verified against the live TV from this
build box** — only mocked subprocess tests. Confirm the `launch_app` play
command on the Hub PC.

## Phase 3 scope (design only, except the stubs noted)

Details in [PHASE3_DESIGN.md](PHASE3_DESIGN.md). Already started in phase 2 as
stubs: samsungtvws power off / Wake-on-LAN power on / `KEY_HDMI` and
`KEY_SOURCE` input (see [TV_CONTROL.md](TV_CONTROL.md)), single-device orbs, and
the samsung-tv-root play bridge above. Still to do:

- Named-input switching on the QN75QN800D (calibrate the `KEY_HDMI` cycle).
- `TvSpecialist` so "turn on the TV" and "switch to HDMI 2" work by voice.
- Multi-node orb UI: active-node breadcrumb and nearest-mic handoff.
- Nighttime Reolink SD-card person detection (10 PM-6 AM, America/New_York).
- A household security-scanning / hardening pass.
- Compose power-on with the playback hook (wake, then play).

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
- Neither listener (`scripts/wakeword.py` or the voice agent) can run inside
  the boot (Session 0) service. They need an interactive logon (or autologon,
  which is a security tradeoff and is not the default). After a reboot the hub
  is up immediately but "hey jarvis" works only once you have logged in.
- First run of each model needs internet once: openWakeWord models (GitHub),
  the faster-whisper model and Piper voice (Hugging Face). INSTALL_WINDOWS.md
  step A7 does these up front; everything is offline afterwards.
- The talkback orb and Talkback toggle need a tap per page load: browsers
  block audio until a user gesture. Replies are only spoken on the device that
  sent the command.
- The Tizen browser over plain HTTP is not a secure context, so the TV's blue
  orb can only follow the hub's microphone state, never the TV's own mic.
- KEY_HDMI1..4 are unreliable on 2024 Samsung sets; the controller refuses them
  unless `direct_hdmi_keys` is set. "Go to HDMI 2" needs phase 3 calibration.
- Wake-word engines are not speaker ID. Until Patrick enrolls, the
  agent skips verification and will accept any voice that says “hey
  jarvis”.
- `ThreadingHTTPServer` holds one thread per SSE client. Fine for a
  handful of household devices, not for a public deployment.
- No transcoding, no subtitle handling, no TV-show season/episode model.
