# Jarvis Hub (Phase 1)

A lightweight, always-on household AI hub that runs on a Windows desktop PC
and serves a local-network dashboard to phones and a Samsung TV browser.

Phase 1 is the plumbing: wake, receive, route, reply, display. It indexes a
local movie library, accepts typed or spoken commands, and returns rule-based
replies. Nothing here requires the cloud. Grok API, TV-root playback, Echo
Show / Fire TV remotes, and a laptop client are reserved for phase 2.

The target machine is a well-built Windows desktop (32 GB RAM, 8 GB VRAM,
large local movie library). The code is Python 3.10+ and stays on the
standard library (`http.server`, `json`, `sqlite3`, `pathlib`, `logging`,
`xml`, `difflib`, `ssl`).

## What you get

- An always-on service that starts at boot (Task Scheduler) and keeps running
  with no user logged in. A watchdog launcher restarts the server on a crash.
- A web dashboard on port **8765** (configurable):
  - Phone view at `http://<hub-ip>:8765` — chat, optional mic, media grid.
  - TV remote view at `http://<hub-ip>:8765/tv` — 10-foot UI for the
    Samsung 75" Neo QLED 8K (`QN75QN800DFXZA`, Tizen 8/9) built-in Internet
    browser. D-pad navigation, no native app.
- Command pipeline with pluggable specialists (core + media; a disabled
  Grok placeholder).
- A media indexer that walks configured library folders, parses titles and
  years, reads Kodi-style `.nfo` files and poster sidecars, and serves
  poster art. "Play X on the TV" resolves to a file path and returns a
  **stub** "would play" action. Video is not streamed in phase 1.

## Hub PC setup (Windows)

### 1. Install Python

Install **Python 3.10 or later** from
[python.org](https://www.python.org/downloads/windows/). During setup,
check **Add python.exe to PATH**. Confirm in a new Command Prompt or
PowerShell:

```powershell
py -3 --version
```

No third-party packages are required. `requirements.txt` is present only
to document that fact.

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
- `auth_token` — optional shared secret. If set, clients must send it as
  the `X-Hub-Token` header or a `?token=` query parameter. Leave `""` for
  an open LAN.
- `ssl` — leave disabled unless you want HTTPS for the phone mic button
  (see below).

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

This phase does **not** talk to a rooted TV or launch a local movie file.
"Play … on the TV" only resolves the file and tells you it *would* play.
That hook is marked in the media specialist for phase 2
(`samsung-tv-root`).

## Commands (phase 1)

| You say / type              | What happens                                      |
| --------------------------- | ------------------------------------------------- |
| `hello`, `hi`, `good morning` | Greeting from the hub                           |
| `what time is it`           | Local clock                                       |
| `what's the date`           | Today's date                                      |
| `help`                      | List of commands                                  |
| `status`                    | Uptime, library size, connected clients           |
| `list movies`               | Titles from the index                             |
| `find <title>`              | Search / fuzzy match                              |
| `play <title> on the tv`    | Resolves a file, returns a stub "would play"      |
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
- `POST /api/media/rescan` — `?wait=1` to block until done; `?full=1` to
  re-parse everything

Poster serving is restricted to paths stored in the index. There is no
arbitrary file read, and no video stream (`/api/media/<id>/stream` returns
501 on purpose).

## Developing / verifying on Linux

```bash
cp config.example.json config.json   # point media_paths at a test folder
python3 -m unittest discover -s tests -v
python3 -m jarvis.server
```

## Docs

- [Architecture](docs/ARCHITECTURE.md) — components, the pipeline, thin
  clients, specialist plug-in points, security.
- [Status](docs/STATUS.md) — what phase 1 shipped vs. what phase 2 should
  cover, plus known limitations.
- [Phase 2 design](docs/PHASE2_DESIGN.md) — wake word, talkback, Grok API, TV playback, clients, transfers, and security.

## License

For household use by Katie. No cloud services are required for phase 1.
