# Jarvis Hub

A lightweight, always-on household AI hub that runs on a Windows desktop PC
and serves a local-network dashboard to phones and a Samsung TV browser.

Phase 1 is the plumbing: wake, receive, route, reply, display, and a local
movie index. **Phase 2 is in this tree** (listed below, with the file that
implements each part). Core function works entirely on the LAN with no
cloud; the Grok specialist stays off unless you set `XAI_API_KEY`.

**Installing on the Hub PC?** Follow [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md)
(copy-paste PowerShell: replace the service, install packages, voice model,
wake word listener).

## Phase 2 contents

If your copy does not have the files in the right-hand column, it is an old
phase 1 download. Get `JarvisHub-phase2.zip` or
`https://github.com/pmyoung1/JarvisHub/archive/refs/heads/main.zip`.

| Feature | What it does | Where it lives |
| ------- | ------------ | -------------- |
| **Wake word listener** | Offline openWakeWord "hey jarvis" -> speech-to-text -> `POST /api/command`. Runs in your login session (not the SYSTEM boot task, which has no microphone). Flags: `--hub-url --token --threshold --list-devices --dry-run`. | `scripts/wakeword.py`, `jarvis/voice/listener.py`, `capture.py`, `wake.py`, `stt.py`, `client.py` |
| **Listener launch scripts** | Run it, add it to `shell:startup`, remove it. | `scripts/run_wakeword.ps1`, `install_wakeword_startup.ps1`, `uninstall_wakeword_startup.ps1` |
| **Full voice agent** (optional) | Wake word + speaker verification + spoken replies on the hub PC + mute sync. | `python -m jarvis.voice` (`jarvis/voice/agent.py`, `speaker.py`, `enroll.py`), `scripts/install_voice_agent.ps1`, `enroll_voice.ps1` |
| **Talkback (Piper, SAPI fallback)** | Offline voices. Hub endpoint returns a WAV: `POST /api/tts` (or `GET /api/tts?text=`). Order: Piper, Windows SAPI (System.Speech), pyttsx3. No edge-tts. | `jarvis/tts_service.py`, `jarvis/voice/tts.py`, config `voice.tts_voice`, `voice.tts_voices_dir`, `voice.tts_model_path`, `tts.*` |
| **Orbs (blue mic, orange talkback)** | Blue orb pulses from an AnalyserNode on the mic stream when speech is detected; orange orb pulses from an AnalyserNode on the talkback `<audio>`. Radial gradients, sine-eased scale. Also follows hub SSE `voice` state; degrades with no mic or on plain HTTP (TV). | `web/js/orbs.js`, `web/css/orbs.css`, wired into `web/index.html` (phone) and `web/tv.html` |
| **TV mic probe** | Checks whether the Tizen Microphone API / `getUserMedia` can reach the TV's microphone from a browser page, vs a packaged Tizen app. | `/tv/mic-probe` (`web/mic-probe.html`, `web/js/mic-probe.js`), `jarvis/mic_probe.py`, `GET`/`POST /api/mic-probe`, `/api/health`, [docs/TV_MIC_PROBE.md](docs/TV_MIC_PROBE.md) |
| **Samsung TV power / input (stubs)** | samsungtvws on wss:8002 with a token file: power off (`KEY_POWER`), input (`KEY_HDMI` cycle, `KEY_SOURCE`). Power on is Wake-on-LAN. Needs "Power On with Mobile" and "IP Remote" on the TV; Ethernet beats Wi-Fi. | `jarvis/tv/samsung.py`, `jarvis/tv/wol.py`, `POST /api/tv/power`, `POST /api/tv/input`, config `tv.ip`, `tv.mac`, `tv.token_file`, [docs/TV_CONTROL.md](docs/TV_CONTROL.md) |
| **samsung-tv-root play bridge** | For an already-rooted TV: `sdb push` + `archive-root root <ip> --command …` so "play &lt;title&gt; on the tv" is a real action. Also `tv on` / `tv off` / input. | `jarvis/tv/root.py`, `jarvis/specialists/tv.py`, config `tv_ip` / `tv_sdb_path` / `tv_root_exe_path` / `tv_runtime`, [docs/TV_ROOT.md](docs/TV_ROOT.md) |
| Grok specialist | Optional replies when `XAI_API_KEY` is set. | `jarvis/specialists/grok.py` |
| Streaming + transfers | HTTP Range streaming, phone-to-TV drop, TV controller hook, per-device tokens. | `jarvis/http_range.py`, `transfers.py`, `auth.py`, `jarvis/tv/controller.py` |
| Thin clients | `/laptop`, `/echo`, `/fire` aliases. | [docs/CLIENTS.md](docs/CLIENTS.md) |

What is **not** verified without the real hardware (microphone, speakers, the
TV) is listed in [docs/STATUS.md](docs/STATUS.md).

### Which listener: `wakeword.py` or the voice agent?

Run **one**. They share a lock (`data/voice/listener.lock`), so a second one
exits instead of answering twice.

| | `scripts/wakeword.py` (default choice) | `python -m jarvis.voice` (full agent) |
| --- | --- | --- |
| Install | `scripts/install_wakeword_startup.ps1` | `scripts/install_voice_agent.ps1` |
| Starts | Startup-folder shortcut at your logon | Scheduled task `JarvisHubVoice` at your logon |
| Packages | `requirements-wakeword.txt` (small) | `requirements-voice.txt` (adds torch + SpeechBrain, several GB) |
| Speaker verification | No: anyone who says "hey jarvis" is heard | Yes, after `enroll_voice.ps1` |
| Spoken replies on the PC | Only with `--speak` | Yes (`speak_sources`) |
| Mute button on the dashboard | Honored | Honored |

Phones and the TV get talkback from the hub's `/api/tts` either way (turn on
**Talkback** on the page).

### Packages

The hub itself stays on the Python standard library: `requirements.txt` has no
packages. Optional add-ons are separate files, all imported lazily, so the hub
starts without them:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-wakeword.txt -r requirements-tv.txt
# Only for the full voice agent with speaker verification:
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
```

| File | Installs |
| ---- | -------- |
| `requirements-wakeword.txt` | `openwakeword`, `onnxruntime`, `faster-whisper`, `piper-tts`, `pyttsx3`, `sounddevice`, `soundfile`, `numpy` |
| `requirements-tv.txt` | `samsungtvws` (LGPL-3.0) |
| `requirements-voice.txt` | the wakeword file plus `torch`, `speechbrain`, `resemblyzer`, `vosk` |

A Piper voice file also has to be downloaded; see step A7 of
[docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md).

The target machine is a well-built Windows desktop (32 GB RAM, 8 GB VRAM,
large local movie library). The hub process is Python 3.10+.

## What you get

- An always-on service that starts at boot (Task Scheduler) and keeps running
  with no user logged in. A watchdog launcher restarts the server on a crash.
- A web dashboard on port **8765** (configurable):
  - Phone view at `http://<hub-ip>:8765` - chat, optional mic, media grid, orbs, talkback.
  - TV remote view at `http://<hub-ip>:8765/tv` - 10-foot UI for the
    Samsung 75" Neo QLED 8K (`QN75QN800DFXZA`, Tizen 8/9) built-in Internet
    browser. D-pad navigation, no native app.
- Command pipeline with pluggable specialists (core + media + Grok when
  an API key is present).
- A media indexer that walks configured library folders, parses titles and
  years, reads Kodi-style `.nfo` files and poster sidecars, and serves
  poster art. "Play X on the TV" resolves a file, issues an HTTP Range
  stream URL, and calls the configured TV controller. Until
  samsung-tv-root is hooked up, the reply stays "would play".

## Hub PC setup (Windows)

### 1. Install Python

Install **Python 3.10 or later** from
[python.org](https://www.python.org/downloads/windows/). During setup,
check **Add python.exe to PATH**. Confirm in a new Command Prompt or
PowerShell:

```powershell
py -3 --version
```

No third-party packages are required for the hub itself.
`requirements.txt` documents that. The phase 2 add-ons are optional (see
"Packages" above).

### 2. Copy the project onto the PC

Copy this folder to a stable path, for example `C:\JarvisHub`. If you are
working from a zip, extract it there (the phase 2 zip has its files at the
root, so it extracts straight into `C:\JarvisHub`). Upgrading from phase 1?
Use the step-by-step in [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md).

### 3. Configure

```powershell
cd C:\JarvisHub
copy config.example.json config.json
notepad config.json
```

Set at least:

- `hub_name` — how the dashboard introduces itself (default `Jarvis`).
- `host` — leave `0.0.0.0` so phones and the TV can reach it on the LAN.
- `port` — default `8765`.
- `media_paths` — one or more folders that hold the movie library, e.g.
  `"D:\\Movies"`. Use doubled backslashes in JSON, or forward slashes
  (`"D:/Movies"`).
- `data_dir` — where the SQLite index and rotating logs go (default
  `data/`, under the repo).
- `auth_token` — optional household secret. If set, clients must send it as
  the `X-Hub-Token` header or a `?token=` query parameter. Leave `""` for
  an open LAN.
- `auth_tokens` — optional per-device secrets (`phone`, `tv`, `laptop`,
  `voice`, `transfers`). Values may be the raw token or `sha256:<hex>`.
- `voice`, `grok`, `tv`, `transfers` — see `config.example.json` and
  [PHASE2_DESIGN.md](docs/PHASE2_DESIGN.md). Leave `tv.controller` as
  `"null"` until samsung-tv-root is installed (or set it to `samsungtvws` for
  power/input, see [docs/TV_CONTROL.md](docs/TV_CONTROL.md)). Grok is used only when
  the environment variable in `grok.api_key_env` (default `XAI_API_KEY`)
  is set.
- `ssl` — leave disabled unless you want HTTPS for the phone mic button
  (see below).
- `public_host` — optional LAN name or IP used when building stream URLs
  (otherwise the request `Host` header is used).

### 4. Test-run in the foreground

```powershell
py -3 -m jarvis.server
```

Or, to also test the watchdog launcher:

```powershell
.\scripts\run.ps1
```

Visit `http://127.0.0.1:8765` on the same PC. `GET /api/health` should
return `{"ok": true, ...}`. Stop with Ctrl+C.

### 5. Install the always-on scheduled task

Open **PowerShell as Administrator** (Task Scheduler and the firewall rule
need elevation):

```powershell
cd C:\JarvisHub
.\scripts\install_service.ps1
```

This registers a task named `JarvisHub` with:

- trigger **AtStartup**
- **Run whether the user is logged on or not** (as `SYSTEM` by default)
- **Restart on failure**, five times, one minute apart
- **no execution time limit**

If the movie library lives on a user-only drive or a logged-in profile,
run the task as that user instead of SYSTEM:

```powershell
.\scripts\install_service.ps1 -User "$env:USERDOMAIN\$env:USERNAME"
```

You may be asked for the account password (needed so Windows can start
the task with no one logged in).

Start it immediately:

```powershell
Start-ScheduledTask -TaskName JarvisHub
```

Logs land in `data\logs\hub.log` (rotating). Remove everything with
`.\scripts\uninstall_service.ps1`.

#### Startup-folder alternative

If you would rather not use Task Scheduler, press Win+R, run
`shell:startup`, and drop a shortcut to
`C:\JarvisHub\scripts\run.ps1` there (or to
`pyw.exe -3 -m jarvis.launcher` with "Start in" set to the repo root).

**This only starts after a user logs in**, so it is a fallback, not the
recommended always-on setup.

### 6. Firewall

`install_service.ps1` already calls `scripts\install_firewall.ps1`, which
allows inbound TCP `8765` on the **Private** profile only. To do it
separately:

```powershell
.\scripts\install_firewall.ps1 -Port 8765
```

Do not expose this port on a Public profile or the internet.

### 7. Give the PC a stable LAN address

On the hub PC, run:

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.PrefixOrigin -ne "WellKnown" } |
  Select-Object IPAddress, InterfaceAlias
```

Note the address on the LAN adapter (often `192.168.x.x` or `10.x.x.x`).
In your router's DHCP settings, add a **reservation** for this PC's MAC
address so the IP does not change. A static address on the adapter itself
also works. You will use this IP on the phone and the TV.


## Voice (phase 2, Hub PC)

Windows will not give a Session 0 boot service access to the microphone.
The hub service stays as it is. The listener is a **separate** process that
starts **when you log on** (see "Which listener" above).

Lightweight listener (default):

```powershell
cd C:\JarvisHub
.\.venv\Scripts\python.exe scripts\wakeword.py --list-devices
powershell -ExecutionPolicy Bypass -File .\scripts\run_wakeword.ps1 -Foreground --dry-run   # test
powershell -ExecutionPolicy Bypass -File .\scripts\install_wakeword_startup.ps1               # add to shell:startup
```

It logs to `logs\wakeword.log`. Flags: `--hub-url`, `--token`, `--threshold`,
`--list-devices`, `--dry-run`, plus `--input-device`, `--speak`, `--stt-model`,
`--stt-device`, `--wav` (feed a WAV instead of the mic), `--log-file`.

Full voice agent with speaker verification:

```powershell
.\scripts\install_voice_agent.ps1
.\scripts\enroll_voice.ps1
Start-ScheduledTask -TaskName JarvisHubVoice
```

The install script creates `.venv`, installs `requirements-voice.txt`,
downloads the default Piper voice (`en_US-lessac-medium`), and registers
an At-logon task named `JarvisHubVoice`. Enrollment records three clips
of Patrick's voice for SpeechBrain ECAPA (Resemblyzer is the fallback). Until
you enroll, the agent skips the voice check.

Set `voice.input_device` and `voice.output_device` in `config.json` if the
defaults are wrong. `speak_sources` defaults to `["hub-mic"]`, so typed phone
commands are not spoken on the PC. Remove the logon task with
`.\scripts\uninstall_voice_agent.ps1`; neither listener touches the `JarvisHub`
boot service.

### Talkback and the orbs

`POST /api/tts` with `{"text": "..."}` (or `GET /api/tts?text=...`) returns a
WAV made by Piper when a voice is installed, else Windows SAPI. Voice files
go in `data\voices\` (config default) or at `voice.tts_model_path`. With
nothing installed it answers `503` with what to install.

On the phone and TV pages, tap **Talkback** to let replies to commands sent
from that device play through the page; the orange orb follows the audio, and
the blue orb follows your microphone when the page may use it (tap **Mic orb**;
needs HTTPS or localhost). Without a mic it follows the hub's own listener state.

### Samsung TV power and input

Off by default. See [docs/TV_CONTROL.md](docs/TV_CONTROL.md) for the TV settings
(**Power On with Mobile**, **IP Remote**, prefer Ethernet), `tv.ip` / `tv.mac` /
`tv.token_file`, pairing, and `POST /api/tv/power` / `POST /api/tv/input`.

## Open the dashboard from a phone

1. Join the same Wi-Fi / LAN as the hub PC.
2. In the phone browser, open `http://<hub-ip>:8765` (for example
   `http://192.168.1.50:8765`).
3. Add it to the home screen if you like (Safari Share → Add to Home
   Screen; Chrome menu → Add to Home screen). A simple web manifest is
   included.

Type a command such as `help`, `list movies`, `find inception`, or
`play inception on the tv`.

### Microphone on the phone

The mic button uses the **Web Speech API** (`SpeechRecognition`). Most
mobile browsers only allow it on **HTTPS** or **localhost**. Over plain
`http://192.168.x.x` the button is disabled on purpose.

Workarounds that do not need HTTPS:

- Type the command.
- Use the **keyboard's dictation key** (the microphone on the on-screen
  keyboard). That path is handled by the OS, not the page, and works on
  HTTP.

Optional HTTPS:

1. Run `.\scripts\generate_cert.ps1` (you may still need OpenSSL, or Git
   for Windows, to export `key.pem`).
2. Set `ssl.enabled` to `true` in `config.json` and restart the hub.
3. Trust `certs\cert.cer` (or `cert.pem`) on the phone, then open
   `https://<hub-ip>:8765`. Self-signed warnings are expected until the
   cert is trusted.

## Open the dashboard from the Samsung TV

Set on the **QN75QN800D** (Tizen 8/9, 2024):

1. Open the built-in **Internet** (web browser) app.
2. Type `http://<hub-ip>:8765/tv` in the address bar.
3. Bookmark the page so you do not have to type it again.
4. Use the remote:
   - **D-pad / arrows** move between the big tiles and posters.
   - **Enter** selects (or sends a typed command).
   - **Back** closes a title overlay. The page also listens for Tizen
     keyCode `10009`, plus Backspace and Escape.

The Tizen browser is unlikely to support the Web Speech API, so the TV
page has **no mic button**. Use the quick-action tiles, or type with the
on-screen keyboard and press Send.

"Play … on the TV" resolves the file and returns an HTTP Range stream
URL (`GET /api/media/<id>/stream`). Playback on the set starts only
when `tv.controller` is `samsung-tv-root` (or another backend) and
`tv.controller_options.command` or `.endpoint` points at that project.
Until then the reply is still "would play".

To test whether the TV's own microphone is usable from this browser,
open `http://<hub-ip>:8765/tv/mic-probe`. See
[docs/TV_MIC_PROBE.md](docs/TV_MIC_PROBE.md). If that probe cannot get
live audio, treat the TV as display-only.

Laptop, Echo Show 15, and Fire TV setup is in
[docs/CLIENTS.md](docs/CLIENTS.md).

## Commands

| You say / type              | What happens                                      |
| --------------------------- | ------------------------------------------------- |
| `hello`, `hi`, `good morning` | Greeting from the hub                           |
| `what time is it`           | Local clock                                       |
| `what's the date`           | Today's date                                      |
| `help`                      | List of commands                                  |
| `status`                    | Uptime, library size, connected clients           |
| `list movies`               | Titles from the index                             |
| `find <title>`              | Search / fuzzy match                              |
| `play <title> on the tv`    | Resolves a file, issues a stream URL, asks the TV controller to play it |
| `jarvis, …`                 | Optional wake-word prefix, stripped before routing |

## Useful endpoints

- `GET /api/health` — liveness for the watchdog and LAN probes
- `GET /api/status` — richer status
- `POST /api/command` — `{ "text", "source", "client_id" }` →
  `{ "reply", "intent", "data", "stages" }`
- `GET /api/events` — Server-Sent Events so the phone and TV stay in sync
- `GET /api/media?q=&limit=&offset=`
- `GET /api/media/<id>`
- `GET /api/media/<id>/poster` — sidecar art or a generated SVG placeholder
- `GET /api/media/<id>/stream` — HTTP Range stream of an **indexed** file
  (optional short-lived `exp`/`sig` query token)
- `POST /api/media/rescan` — `?wait=1` to block until done; `?full=1` to
  re-parse everything
- `POST /api/transfers` — phone upload (multipart or raw + `X-Filename`)
- `GET /api/transfers`, `GET /api/transfers/<id>/stream`,
  `POST /api/transfers/<id>/play`, `DELETE /api/transfers/<id>`
- `GET`/`POST /api/voice/status`, `POST /api/voice/mute`
- `POST /api/tts` (`{"text"}`) or `GET /api/tts?text=` - talkback WAV (Piper, SAPI fallback)
- `POST /api/tv/power` (`{"action":"on|off"}`), `POST /api/tv/input`
  (`{"input":"next|source|hdmi1..4","times":1}`), `GET /api/tv/status` - Samsung
  controller, 501 unless `tv.controller` is `samsungtvws`
- `GET`/`POST /api/mic-probe` — TV microphone diagnostic reports
- `GET /tv/mic-probe` — 10-foot probe page for the TV Internet app

Poster and video serving are restricted to paths stored in the index or
the transfer store. There is no arbitrary file read.

## Developing / verifying on Linux

```bash
cp config.example.json config.json   # point media_paths at a test folder
python3 -m unittest discover -s tests -v
python3 -m jarvis.server
```

## Docs

- [Architecture](docs/ARCHITECTURE.md) — components, the pipeline, thin
  clients, specialist plug-in points, security.
- [Install on Windows](docs/INSTALL_WINDOWS.md) - copy-paste PowerShell for the Hub PC.
- [Status](docs/STATUS.md) — phase 2 progress, hardware checklist, and later work.
- [TV control](docs/TV_CONTROL.md) - samsungtvws power/input and Wake-on-LAN setup.
- [TV root](docs/TV_ROOT.md) - samsung-tv-root archive-root play bridge (OpenSSL PATH, paths, unverified launch command).
- [Phase 2 design](docs/PHASE2_DESIGN.md) — wake word, talkback, Grok API, TV playback, clients, transfers, and security.
- [Thin clients](docs/CLIENTS.md) — phone, TV, laptop, Echo Show 15, Fire TV.
- [TV mic probe](docs/TV_MIC_PROBE.md) — diagnostic for the QN75QN800D built-in microphone.
- [Phase 3 design](docs/PHASE3_DESIGN.md) — Samsung TV power, HDMI, Wake-on-LAN, dual-orb UI, Reolink nighttime detection, security scanning.

## License

For household use by Katie. No cloud services are required for core function.
