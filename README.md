# Jarvis Hub

A lightweight, always-on household AI hub that runs on a Windows desktop PC
and serves a local-network dashboard to phones and a Samsung TV browser.

Phase 1 is the plumbing: wake, receive, route, reply, display, and a local
movie index. Phase 2 adds an interactive voice agent on the Hub PC (wake
word, speaker check, talkback), optional Grok replies, HTTP Range
streaming with a TV controller hook, phone-to-TV transfers, and thin
clients for a laptop, Echo Show, and Fire TV. Core function still works
entirely on the LAN with no cloud. The Grok specialist stays off unless
you set `XAI_API_KEY`.

The target machine is a well-built Windows desktop (32 GB RAM, 8 GB VRAM,
large local movie library). The hub process is Python 3.10+ and stays on the standard library
(`http.server`, `json`, `sqlite3`, `pathlib`, `logging`, `xml`,
`difflib`, `ssl`). Optional voice libraries are listed in
`requirements-voice.txt` and are imported only by the voice agent.

## What you get

- An always-on service that starts at boot (Task Scheduler) and keeps running
  with no user logged in. A watchdog launcher restarts the server on a crash.
- A web dashboard on port **8765** (configurable):
  - Phone view at `http://<hub-ip>:8765` — chat, optional mic, media grid.
  - TV remote view at `http://<hub-ip>:8765/tv` — 10-foot UI for the
    Samsung 75" Neo QLED 8K (`QN75QN800DFXZA`, Tizen 8/9) built-in Internet
    browser. D-pad navigation, no native app.
- Command pipeline with pluggable specialists (core + media + Grok when
  an API key is present).
- A media indexer that walks configured library folders, parses titles and
  years, reads Kodi-style `.nfo` files and poster sidecars, and serves
  poster art. "Play X on the TV" resolves a file, issues an HTTP Range
  stream URL, and calls the configured TV controller. Until
  samsung-tv-root is hooked up, the reply stays "would play".
- An optional voice agent (`python -m jarvis.voice`) that must run in a
  logged-on desktop session, not in the boot service.

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
`requirements.txt` documents that. Voice extras are optional
(`requirements-voice.txt`).

### 2. Copy the project onto the PC

Copy this folder to a stable path, for example `C:\JarvisHub`. If you are
working from a zip, extract it there.

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
  `"null"` until samsung-tv-root is installed. Grok is used only when
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


## Voice agent (phase 2, Hub PC)

Windows will not give a Session 0 boot service access to the microphone.
The hub service stays as it is. The voice agent is a **separate**
process that starts **when someone logs on**.

```powershell
cd C:\JarvisHub
.\scripts\install_voice_agent.ps1
.\scripts\enroll_voice.ps1
Start-ScheduledTask -TaskName JarvisHubVoice
```

The install script creates `.venv`, installs `requirements-voice.txt`,
downloads the default Piper voice (`en_US-lessac-medium`), and registers
an At-logon task named `JarvisHubVoice`. Enrollment records three clips
of Patrick's voice for SpeechBrain ECAPA (Resemblyzer is the fallback).

Until you enroll, the agent logs a warning and skips the voice check
(anyone who says "hey jarvis" is accepted). After enrollment, only a
close match to the saved profile is transcribed and sent to the hub.

List devices:

```powershell
.\.venv\Scripts\python.exe -m jarvis.voice --list-devices
```

Set `voice.input_device` and `voice.output_device` in `config.json` if
needed. Talkback uses Piper first, then Windows SAPI via pyttsx3.
`speak_sources` defaults to `["hub-mic"]`, so typed phone commands are
not spoken aloud unless you add those sources.

Remove the logon task with `.\scripts\uninstall_voice_agent.ps1`. This
does not touch the `JarvisHub` boot service.

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
- [Status](docs/STATUS.md) — phase 2 progress, hardware checklist, and later work.
- [Phase 2 design](docs/PHASE2_DESIGN.md) — wake word, talkback, Grok API, TV playback, clients, transfers, and security.
- [Thin clients](docs/CLIENTS.md) — phone, TV, laptop, Echo Show 15, Fire TV.
- [TV mic probe](docs/TV_MIC_PROBE.md) — diagnostic for the QN75QN800D built-in microphone.
- [Phase 3 design](docs/PHASE3_DESIGN.md) — Samsung TV power, HDMI, Wake-on-LAN, dual-orb UI, Reolink nighttime detection, security scanning.

## License

For household use by Katie. No cloud services are required for core function.
