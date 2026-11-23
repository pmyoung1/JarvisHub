# Samsung TV root integration (phase 2 ↔ 3 bridge)

This is how Jarvis Hub talks to a **already-rooted** Samsung TV through
[`samsung-tv-root`](https://github.com/chris-ritsen/samsung-tv-root) `archive-root`.
It is **not** a root exploit. The Hub PC at this household already has a working
root session on the QN75QN800DFXZA (`10.0.0.7`, Tizen 6.0 runtime); the hub only
shells out to the release executable the same way a human would.

Verified on the Hub PC (not from this Linux build box): samsung-tv-root **0.0.13**,
SDB at `C:\sdb\data\tools\sdb.exe`, root session `uid 0`, full capabilities,
SMACK `User`, system files unchanged, staging cleaned.

Code: `jarvis/tv/root.py` (`RootShell`, `ArchiveRootController`). Set
`tv.controller` to `root` (aliases: `samsung-tv-root`, `archive-root`).

## Exact host command pattern

```text
<exe> --sdb <sdb_path> archive-root root <tv_ip> --command "<cmd>"
```

Example:

```powershell
& "C:\samsung-tv-root-release\samsung-tv-root-0.0.13-windows-x86_64\samsung-tv-root.exe" `
  --sdb "C:\sdb\data\tools\sdb.exe" `
  archive-root root 10.0.0.7 `
  --command "id; uname -a"
```

`--sdb` is a **global** flag and must come **before** `archive-root`. The hub
builds this as a Python argument list (`subprocess.run(..., shell=False)`); it
never passes the command through `cmd.exe`.

`sdb push` (for movie files) uses:

```text
<sdb> -s <tv_ip>:26101 push <local> <remote>
```

## Config keys

Any of these work; top-level names win when set. Nested `tv.*` is preferred for
new configs. Defaults match this household's paths.

| Key | Default | Meaning |
| --- | ------- | ------- |
| `tv_ip` / `tv.ip` | `10.0.0.7` | TV LAN address |
| `tv_sdb_path` / `tv.sdb_path` | `C:\sdb\data\tools\sdb.exe` | Samsung SDB |
| `tv_root_exe_path` / `tv.root_exe_path` | `C:\samsung-tv-root-release\samsung-tv-root-0.0.13-windows-x86_64\samsung-tv-root.exe` | Release binary |
| `tv_runtime` / `tv.runtime` | `6.0` | Tizen runtime (metadata / status) |
| `tv.mac` | _(empty)_ | Ethernet MAC for Wake-on-LAN power-on |
| `tv.controller` | `null` | Set to `root` to enable this backend |
| `tv.controller_options.remote_dir` | `/home/owner/share/tmp/jarvis` | Where `sdb push` drops files |
| `tv.controller_options.player_app` | `org.tizen.video-player-tv` | Tizen video player app id |
| `tv.controller_options.play_command_template` | see below | Root shell command after push |
| `tv.controller_options.use_samsungtvws_keys` | `true` | Also use samsungtvws for KEY_POWER / KEY_HDMI when installed |

`tv.ip` / `tv.mac` / `tv.token_file` from the samsungtvws stub remain valid. With
`controller: "root"` the hub uses archive-root for playback and Wake-on-LAN for
power-on; power-off and input prefer samsungtvws keys when that library is
installed (`requirements-tv.txt`).

Enable it:

```json
"tv_ip": "10.0.0.7",
"tv_sdb_path": "C:\\sdb\\data\\tools\\sdb.exe",
"tv_root_exe_path": "C:\\samsung-tv-root-release\\samsung-tv-root-0.0.13-windows-x86_64\\samsung-tv-root.exe",
"tv_runtime": "6.0",
"tv": {
  "controller": "root",
  "ip": "10.0.0.7",
  "mac": "aa:bb:cc:dd:ee:ff",
  "token_file": "data/tv/samsung_token.txt"
}
```

## Play command (best-effort)

After resolving a title in the media index (`D:\Movies`, `E:\`, …), the hub:

1. `sdb push` the file to `/home/owner/share/tmp/jarvis/<filename>`
2. Runs this root command (template is configurable):

```text
mkdir -p /home/owner/share/tmp/jarvis && launch_app org.tizen.video-player-tv /home/owner/share/tmp/jarvis/<filename>
```

**TODO (hardware):** confirm on the QN75QN800D that `launch_app org.tizen.video-player-tv <path>`
actually starts playback. If the firmware wants a `file://` URI, a different app
id, or `vd_applaunch`, change `tv.controller_options.play_command_template`. The
exact command string is always logged under `[tv]` in `data/logs/hub.log`.

## OpenSSL PATH requirement

The samsung-tv-root release binary needs OpenSSL on the process PATH:

```text
C:\Program Files\OpenSSL-Win64\bin
```

Add that directory to the system or user PATH (or ensure it is present before
starting the hub). The hub also prepends it to PATH for the child process when
the folder exists. Without it, archive-root typically fails during the signed
archive step.

## Venv / Python note

samsung-tv-root itself is the **standalone** `.exe` (or a separate project venv
under `C:\samsung-tv-root\...`). Jarvis Hub does **not** import it as a Python
package. The hub's own `.venv` only needs `requirements-tv.txt` if you also want
samsungtvws keys for power-off / input. Keep the samsung-tv-root project venv
activated only when you run that project's own tools by hand; the hub calls the
release exe directly.

## Logging

Every root and sdb invocation is logged to `data/logs/hub.log` with a `[tv]`
prefix: the full argv, return code, elapsed time, and clipped stdout/stderr.

## Voice / text commands

| Command | Action |
| ------- | ------ |
| `play <title> on the tv` | Resolve → sdb push → root launch |
| `tv on` / `turn the tv on` | Wake-on-LAN to `tv.mac` |
| `tv off` / `turn the tv off` | KEY_POWER via samsungtvws (or best-effort root fallback) |
| `next input` / `switch input` / `source menu` | KEY_HDMI / KEY_SOURCE |

## Graceful failure

If the TV is off, SDB is stuck, the exe is missing, or a command times out, the
hub returns a clear dashboard reply (for example
`I found Inception (2010) but could not start it on the TV: …`). It never
crashes the request thread.

## Phase note

This bridges **phase 2** (real "play on the TV" instead of `would_play`) and
**phase 3** (local TV power / input). Multi-node orbs, Reolink nighttime
detection, and security scanning stay phase 3 design-only — see
[PHASE3_DESIGN.md](PHASE3_DESIGN.md).
